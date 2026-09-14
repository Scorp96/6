"""Deterministic Master A control surface for the V4 transaction core.

The controller deliberately does not contain model logic.  A GPT host supplies a
validated plan and a response decoder; this module owns the durable lifecycle
around those inputs: session fencing, graph admission, two-slot dispatch,
browser-intent recovery, structured-result verification, and completion checks.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .models import AcceptanceStatus, CommitResult, sha256_json
from .scheduler import AssignmentClaim, SchedulerError, WorkerFenceError


class ControllerRejected(ValueError):
    """Raised when a GPT-supplied plan or Worker response is not admissible."""


@dataclasses.dataclass(frozen=True)
class ControllerStep:
    """Machine-readable outcome of one bounded controller scheduling pass."""

    status: str
    master_epoch: int
    claims: tuple[str, ...] = ()
    outcomes: tuple[dict[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()


class MasterAController:
    """Orchestrate one logical Master A through an injected V4 gateway.

    The gateway is intentionally duck-typed so the controller can be tested
    without a browser and can be hosted by either the current Chrome Use seam
    or a Windows MCP adapter.  No callback is allowed to write SQLite directly;
    all authoritative writes go through the gateway methods below.
    """

    _PLAN_KEYS = frozenset({"project_id", "master_identity", "tasks", "transition_id"})
    _TASK_KEYS = frozenset(
        {
            "task_id",
            "objective_sha256",
            "resource_scope",
            "access_mode",
            "dependencies",
            "required",
            "acceptance_criteria_ids",
        }
    )

    def __init__(self, gateway: Any, session_id: str):
        self.gateway = gateway
        self.project_id = str(getattr(gateway, "project_id", "") or "").strip()
        self.session_id = str(session_id or "").strip()
        if not self.project_id:
            raise ControllerRejected("PROJECT_ID_MISSING")
        if not self.session_id:
            raise ControllerRejected("MASTER_SESSION_ID_MISSING")
        self.master_epoch: int | None = None
        self.plan_hash: str | None = None

    def start(
        self,
        root_contract: Mapping[str, Any],
        acceptance_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create/verify the immutable contract and acquire the Master lease."""

        if not isinstance(root_contract, Mapping) or not root_contract:
            raise ControllerRejected("ROOT_CONTRACT_INVALID")
        if not isinstance(acceptance_contract, Mapping) or not acceptance_contract:
            raise ControllerRejected("ACCEPTANCE_CONTRACT_INVALID")
        self.gateway.ensure_contract(dict(root_contract), dict(acceptance_contract))
        session = self.gateway.start_master_session(self.session_id)
        try:
            self.master_epoch = int(session["master_epoch"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ControllerRejected("MASTER_EPOCH_MISSING") from exc
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "master_epoch": self.master_epoch,
            "state": str(session.get("state") or "ACTIVE"),
        }

    def heartbeat(self) -> dict[str, Any]:
        epoch = self._require_epoch()
        return self.gateway.heartbeat_master_session(
            self.session_id, master_epoch=epoch
        )

    def resume(self) -> dict[str, Any]:
        """Reacquire the durable Master session after a physical restart."""

        session = self.gateway.start_master_session(self.session_id)
        try:
            self.master_epoch = int(session["master_epoch"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ControllerRejected("MASTER_EPOCH_MISSING") from exc
        claims = self.gateway.load_worker_claims(master_epoch=self.master_epoch)
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "master_epoch": self.master_epoch,
            "active_claims": tuple(claim.assignment_id for claim in claims),
        }

    def watchdog_once(self) -> dict[str, Any]:
        return self.gateway.watchdog_once()

    def apply_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and durably admit a GPT-produced task graph."""

        normalized = self._validate_plan(plan)
        epoch = self._require_epoch()
        transition_id = str(normalized.get("transition_id") or "").strip()
        if not transition_id:
            transition_id = "master-plan-" + hashlib.sha256(
                canonical_plan(normalized).encode("utf-8")
            ).hexdigest()[:32]
        description = self.gateway.describe()
        expected_version = int(description["state_version"])
        proposal = {
            "project_id": self.project_id,
            "master_identity": "A",
            "kind": "TASK_GRAPH",
            "task_ids": [task["task_id"] for task in normalized["tasks"]],
            "plan_sha256": sha256_json(normalized),
        }
        result = self.gateway.commit_master_proposal(
            transition_id,
            proposal,
            expected_version=expected_version,
            master_epoch=epoch,
        )
        if result not in {CommitResult.COMMITTED, CommitResult.ALREADY_COMMITTED}:
            raise ControllerRejected(f"MASTER_PLAN_COMMIT_{str(result)}")
        self.gateway.enqueue_graph(normalized["tasks"])
        self.plan_hash = sha256_json(normalized)
        return {
            "project_id": self.project_id,
            "master_epoch": epoch,
            "transition_id": transition_id,
            "plan_sha256": self.plan_hash,
            "task_ids": tuple(task["task_id"] for task in normalized["tasks"]),
        }

    def step(
        self,
        worker_prompt_factory: Callable[[AssignmentClaim], str],
        worker_response_decoder: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> ControllerStep:
        """Run one bounded dispatch/reconcile/verify pass.

        Existing claims are loaded first.  A claim with an unresolved browser
        intent is reported as a blocker and is never submitted a second time.
        Only a captured response is decoded and admitted as a Worker result.
        """

        epoch = self._require_epoch()
        watchdog = self.gateway.watchdog_once()
        if str(watchdog.get("status")) != "MASTER_ACTIVE":
            return ControllerStep(
                status="BLOCKED" if str(watchdog.get("status")) != "TERMINAL" else "TERMINAL",
                master_epoch=epoch,
                blockers=(f"MASTER_{str(watchdog.get('status') or 'UNKNOWN')}",),
            )
        active = list(self.gateway.load_worker_claims(master_epoch=epoch))
        free = max(0, 2 - len(active))
        claims = active + (list(self.gateway.claim_workers(master_epoch=epoch, limit=free)) if free else [])
        outcomes: list[dict[str, Any]] = []
        blockers: list[str] = []
        for claim in claims:
            try:
                outcome = self._dispatch_claim(claim, worker_prompt_factory, worker_response_decoder)
            except (ControllerRejected, SchedulerError, WorkerFenceError) as exc:
                blockers.append(f"{claim.task_id}:{str(exc)}")
                continue
            if outcome.get("status") == "BLOCKED":
                blockers.append(f"{claim.task_id}:{outcome.get('reason', 'BLOCKED')}")
            outcomes.append(outcome)
        if blockers:
            status = "BLOCKED"
        elif outcomes:
            status = "DISPATCHED"
        else:
            status = "IDLE"
        return ControllerStep(
            status=status,
            master_epoch=epoch,
            claims=tuple(claim.assignment_id for claim in claims),
            outcomes=tuple(outcomes),
            blockers=tuple(blockers),
        )

    def completion(
        self,
        *,
        candidate_commit: str,
        artifact_hashes: Mapping[str, str],
    ):
        """Run the independent read-only completion gate."""

        return self.gateway.evaluate_completion(
            candidate_commit=candidate_commit,
            artifact_hashes=artifact_hashes,
        )

    def run_cycles(
        self,
        worker_prompt_factory: Callable[[AssignmentClaim], str],
        worker_response_decoder: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        *,
        max_cycles: int = 32,
    ) -> tuple[ControllerStep, ...]:
        """Advance the autonomous loop until it blocks, idles, or hits a cap.

        The cap is mandatory protection against a faulty planner or a task graph
        that keeps producing work.  A persistent monitor can call this method
        again after a durable resume decision; it never turns a blocker into a
        retry or completion by itself.
        """

        try:
            limit = int(max_cycles)
        except (TypeError, ValueError) as exc:
            raise ControllerRejected("MAX_CYCLES_INVALID") from exc
        if limit < 1:
            raise ControllerRejected("MAX_CYCLES_INVALID")
        history: list[ControllerStep] = []
        for _ in range(limit):
            result = self.step(worker_prompt_factory, worker_response_decoder)
            history.append(result)
            if result.status in {"BLOCKED", "IDLE", "TERMINAL"}:
                break
        return tuple(history)

    def _dispatch_claim(
        self,
        claim: AssignmentClaim,
        worker_prompt_factory: Callable[[AssignmentClaim], str],
        worker_response_decoder: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        prompt = str(worker_prompt_factory(claim) or "").strip()
        if not prompt:
            raise ControllerRejected("WORKER_PROMPT_EMPTY")
        intent = self.gateway.prepare_worker_intent(
            claim,
            prompt,
            metadata={"controller": "MasterAController", "required_response": "WORK_RESULT/1"},
        )
        intent_id = str(intent["intent_id"])
        persisted = self.gateway.store.get_intent(intent_id)
        persisted_state = str(persisted.get("state") or "")
        if persisted_state in {"MAY_HAVE_SUBMITTED", "BLOCKED_AMBIGUOUS", "CONFIRMED_SUBMITTED"}:
            current = persisted
        elif persisted_state == "RESPONSE_CAPTURED":
            current = persisted
        else:
            current = self.gateway.submit_intent(intent_id)
        state = str(current.get("state") or "")
        if state in {"MAY_HAVE_SUBMITTED", "BLOCKED_AMBIGUOUS", "CONFIRMED_SUBMITTED"}:
            return {
                "status": "BLOCKED",
                "reason": "BROWSER_RECONCILIATION_REQUIRED",
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "intent_id": intent_id,
                "intent_state": state,
            }
        if state != "RESPONSE_CAPTURED":
            return {
                "status": "BLOCKED",
                "reason": "WORKER_RESPONSE_NOT_CAPTURED",
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "intent_id": intent_id,
                "intent_state": state,
            }
        captured = self.gateway.store.get_intent(intent_id)
        raw_response = captured.get("response_json")
        if not raw_response:
            raise ControllerRejected("WORK_RESULT_RESPONSE_MISSING")
        try:
            response = json.loads(str(raw_response))
        except (TypeError, ValueError) as exc:
            raise ControllerRejected("WORK_RESULT_RESPONSE_INVALID_JSON") from exc
        if not isinstance(response, Mapping):
            raise ControllerRejected("WORK_RESULT_RESPONSE_NOT_MAPPING")
        payload = worker_response_decoder(captured)
        if not isinstance(payload, Mapping):
            raise ControllerRejected("WORK_RESULT_DECODER_NOT_MAPPING")
        if str(payload.get("work_result_version") or "") != "1":
            raise ControllerRejected("WORK_RESULT_VERSION_UNSUPPORTED")
        result_id = self.gateway.record_structured_worker_result(claim, payload=payload)
        self.gateway.verify_worker_result(
            result_id,
            result_sha256=str(payload.get("result_sha256") or ""),
        )
        return {
            "status": "ACCEPTED",
            "assignment_id": claim.assignment_id,
            "task_id": claim.task_id,
            "intent_id": intent_id,
            "intent_state": state,
            "result_id": result_id,
        }

    def _require_epoch(self) -> int:
        if self.master_epoch is None:
            raise ControllerRejected("MASTER_SESSION_NOT_STARTED")
        return int(self.master_epoch)

    def _validate_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, Mapping):
            raise ControllerRejected("PLAN_NOT_MAPPING")
        unknown = set(plan) - self._PLAN_KEYS
        if unknown:
            raise ControllerRejected("PLAN_UNKNOWN_AUTHORITY_FIELD")
        if str(plan.get("project_id") or "").strip() != self.project_id:
            raise ControllerRejected("PLAN_PROJECT_ID_MISMATCH")
        if str(plan.get("master_identity") or "") != "A":
            raise ControllerRejected("MASTER_IDENTITY_INVALID")
        raw_tasks = plan.get("tasks")
        if not isinstance(raw_tasks, Sequence) or isinstance(raw_tasks, (str, bytes)) or not raw_tasks:
            raise ControllerRejected("PLAN_TASKS_INVALID")
        normalized_tasks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_tasks:
            if not isinstance(raw, Mapping):
                raise ControllerRejected("PLAN_TASK_NOT_MAPPING")
            if set(raw) - self._TASK_KEYS:
                raise ControllerRejected("TASK_UNKNOWN_AUTHORITY_FIELD")
            task = dict(raw)
            task_id = str(task.get("task_id") or "").strip()
            if not task_id or task_id in seen:
                raise ControllerRejected("TASK_ID_INVALID_OR_DUPLICATE")
            objective = str(task.get("objective_sha256") or "").strip().lower()
            if len(objective) != 64 or any(char not in "0123456789abcdef" for char in objective):
                raise ControllerRejected("TASK_OBJECTIVE_SHA256_INVALID")
            criteria = task.get("acceptance_criteria_ids", [])
            if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes)):
                raise ControllerRejected("TASK_ACCEPTANCE_IDS_INVALID")
            task["task_id"] = task_id
            task["objective_sha256"] = objective
            task["acceptance_criteria_ids"] = [str(item) for item in criteria]
            scope = task.get("resource_scope", [])
            if not isinstance(scope, Sequence) or isinstance(scope, (str, bytes)):
                raise ControllerRejected("TASK_RESOURCE_SCOPE_INVALID")
            task["resource_scope"] = [str(item) for item in scope]
            seen.add(task_id)
            normalized_tasks.append(task)
        known = set(seen)
        for task in normalized_tasks:
            dependencies = task.get("dependencies", [])
            if not isinstance(dependencies, Sequence) or isinstance(dependencies, (str, bytes)):
                raise ControllerRejected("TASK_DEPENDENCIES_INVALID")
            if any(str(dep) not in known for dep in dependencies):
                raise ControllerRejected("TASK_DEPENDENCY_NOT_IN_PLAN")
        result = dict(plan)
        result["project_id"] = self.project_id
        result["master_identity"] = "A"
        result["tasks"] = normalized_tasks
        return result


def canonical_plan(value: Mapping[str, Any]) -> str:
    """Canonical plan text used for deterministic transition identity."""

    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
