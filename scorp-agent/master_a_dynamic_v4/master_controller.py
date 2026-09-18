"""Deterministic Master A control surface for the V4 transaction core.

The controller deliberately does not contain model logic.  A GPT host supplies a
validated plan and a response decoder; this module owns the durable lifecycle
around those inputs: session fencing, graph admission, two-slot dispatch,
browser-intent recovery, structured-result verification, and completion checks.
"""

from __future__ import annotations

import dataclasses
import concurrent.futures
import hashlib
import json
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .models import CommitResult, sha256_json
from .scheduler import AssignmentClaim, SchedulerError, WorkerFenceError
from .execution_adapter import ExecutionAdapterRejected
from .git_worktree import GitWorktreeRejected
from .work_result import decode_work_result_response, result_content_sha256


class ControllerRejected(ValueError):
    """Raised when a GPT-supplied plan or Worker response is not admissible."""


_WORK_RESULT_LIST_FIELDS = (
    "scope_completed",
    "scope_not_completed",
    "deliverables",
    "evidence",
    "acceptance_coverage",
    "facts",
    "inferences",
    "unknowns",
    "contradictions",
    "followup_proposals",
)
_OPTIONAL_WORK_RESULT_LIST_FIELDS = frozenset(
    {
        "scope_not_completed",
        "deliverables",
        "facts",
        "inferences",
        "unknowns",
        "contradictions",
        "followup_proposals",
    }
)


def normalize_worker_result_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize scalar/list forms before computing the result content hash.

    Web models commonly emit JSON-compatible scalar variants such as numeric
    ``work_result_version`` or string ``base_state_version``.  The scheduler's
    contract validator normalizes those values before storage; doing the same
    here ensures the hash covers the exact envelope that SQLite will verify.
    Missing or invalid fields remain untouched so the validator can reject them
    fail-closed rather than this compatibility step inventing evidence.
    """

    result = dict(value)
    for key in ("work_result_version", "project_id", "assignment_id", "task_id", "worker_id"):
        if key in result and result[key] is not None:
            result[key] = str(result[key])
    for key in ("objective_sha256", "candidate_commit"):
        if key in result and result[key] is not None:
            result[key] = str(result[key]).strip().lower()
    if "base_state_version" in result and not isinstance(result["base_state_version"], bool):
        try:
            result["base_state_version"] = int(result["base_state_version"])
        except (TypeError, ValueError):
            pass
    if "status" in result and result["status"] is not None:
        result["status"] = str(result["status"]).strip().upper()
    for key in _WORK_RESULT_LIST_FIELDS:
        current = result.get(key)
        if current is None and key in _OPTIONAL_WORK_RESULT_LIST_FIELDS:
            result[key] = []
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            result[key] = list(current)
    return result


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
            "task_context",
        }
    )

    def __init__(
        self,
        gateway: Any,
        session_id: str,
        *,
        execution_adapter: Any | None = None,
        git_worktree_manager: Any | None = None,
    ):
        self.gateway = gateway
        self.project_id = str(getattr(gateway, "project_id", "") or "").strip()
        self.session_id = str(session_id or "").strip()
        if not self.project_id:
            raise ControllerRejected("PROJECT_ID_MISSING")
        if not self.session_id:
            raise ControllerRejected("MASTER_SESSION_ID_MISSING")
        self.master_epoch: int | None = None
        self.plan_hash: str | None = None
        self.execution_adapter = execution_adapter
        self.git_worktree_manager = git_worktree_manager

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

    def _master_heartbeat_interval_seconds(self) -> float:
        configured = getattr(self.gateway, "master_heartbeat_interval_seconds", None)
        try:
            value = configured() if callable(configured) else configured
            interval = float(value)
        except (TypeError, ValueError):
            interval = 15.0
        if interval <= 0:
            interval = 15.0
        return max(0.01, min(interval, 30.0))

    @staticmethod
    def _assert_step_authority(authority_lost: Any | None) -> None:
        if authority_lost is not None and bool(authority_lost.is_set()):
            raise ControllerRejected("MASTER_HEARTBEAT_FAILED")

    def attach_existing_session(self) -> dict[str, Any]:
        """Adopt an already-live Master lease after a monitor restart.

        A local supervisor may be restarted without creating a new logical
        session.  It must first read the durable watchdog record and adopt its
        epoch only when the record positively says ``MASTER_ACTIVE``.  Expired
        or unknown states remain for ``MasterSupervisor`` to handle.
        """

        observed = self.gateway.watchdog_once()
        if not isinstance(observed, Mapping):
            raise ControllerRejected("WATCHDOG_RESULT_INVALID")
        if str(observed.get("status") or "") != "MASTER_ACTIVE":
            return dict(observed)
        try:
            self.master_epoch = int(observed["master_epoch"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ControllerRejected("MASTER_EPOCH_MISSING") from exc
        observed_session_id = str(observed.get("session_id") or "").strip()
        if not observed_session_id:
            raise ControllerRejected("MASTER_SESSION_ID_MISSING")
        # A daemon process restart is not a logical Master restart.  The
        # durable watchdog may point at a resumed physical session id that is
        # different from the install-time logical prefix.  Adopt both durable
        # identity fields before any heartbeat so the restarted monitor renews
        # the existing lease instead of heartbeating the stale configured id.
        self.session_id = observed_session_id
        return dict(observed)

    def resume(self) -> dict[str, Any]:
        """Reacquire the durable Master session after a physical restart."""

        previous_session_id = self.session_id
        logical_session_prefix = previous_session_id.split("::resume-", 1)[0]
        replacement_session_id = f"{logical_session_prefix}::resume-{uuid.uuid4().hex[:16]}"
        # A physical ChatGPT/browser session is disposable.  Reusing a stale
        # session id is deliberately rejected by StateStore, so replacement
        # must obtain a fresh id while keeping the logical Master A identity
        # and project epoch bound to the same controller.
        session = self.gateway.start_master_session(replacement_session_id)
        try:
            self.master_epoch = int(session["master_epoch"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ControllerRejected("MASTER_EPOCH_MISSING") from exc
        self.session_id = replacement_session_id
        claims = self.gateway.load_worker_claims(master_epoch=self.master_epoch)
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "previous_session_id": previous_session_id,
            "physical_session_replaced": True,
            "master_epoch": self.master_epoch,
            "active_claims": tuple(claim.assignment_id for claim in claims),
        }

    def end(self, *, reason: str) -> dict[str, Any]:
        """End this epoch when physical-session recovery did not complete.

        A supervisor uses this fail-closed cleanup after it has acquired a new
        epoch but cannot bind a browser. Leaving that epoch ACTIVE would let a
        later monitor heartbeat an unbound logical Master and falsely report
        recovery.
        """

        epoch = self._require_epoch()
        why = str(reason or "").strip()
        if not why:
            raise ControllerRejected("MASTER_END_REASON_MISSING")
        return self.gateway.end_master_session(
            self.session_id,
            master_epoch=epoch,
            reason=why,
        )

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
        atomic_admit = getattr(self.gateway, "commit_master_proposal_and_enqueue", None)
        if callable(atomic_admit):
            result = atomic_admit(
                transition_id,
                proposal,
                normalized["tasks"],
                expected_version=expected_version,
                master_epoch=epoch,
            )
        else:
            # Compatibility fakes/older adapters retain the old two-step API;
            # the production V4 gateway always takes the atomic path above.
            result = self.gateway.commit_master_proposal(
                transition_id,
                proposal,
                expected_version=expected_version,
                master_epoch=epoch,
            )
            if result in {CommitResult.COMMITTED, CommitResult.ALREADY_COMMITTED}:
                self.gateway.enqueue_graph(normalized["tasks"])
        if result not in {CommitResult.COMMITTED, CommitResult.ALREADY_COMMITTED}:
            raise ControllerRejected(f"MASTER_PLAN_COMMIT_{str(result)}")
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
        worker_response_decoder: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
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
        recover_captured = getattr(self.gateway, "recover_captured_response_claims", None)
        if callable(recover_captured):
            recover_captured(master_epoch=epoch)
        active = list(self.gateway.load_worker_claims(master_epoch=epoch))
        free = max(0, 2 - len(active))
        claims = active + (list(self.gateway.claim_workers(master_epoch=epoch, limit=free)) if free else [])
        outcomes: list[dict[str, Any]] = []
        blockers: list[str] = []
        authority_lost = threading.Event()
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[BaseException] = []

        def keep_master_live() -> None:
            interval = self._master_heartbeat_interval_seconds()
            while not heartbeat_stop.wait(interval):
                try:
                    self.heartbeat()
                except BaseException as exc:
                    heartbeat_errors.append(exc)
                    authority_lost.set()
                    return

        heartbeat_thread = threading.Thread(
            target=keep_master_live,
            name="scorp-v4-master-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            # Browser I/O is the slow boundary. Dispatch independent claims
            # concurrently while the Master lease keeper proves this physical
            # controller is still alive. A lost heartbeat fences all later
            # result/local-execution admission but never resubmits an intent.
            if len(claims) > 1:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(2, len(claims)),
                    thread_name_prefix="scorp-v4-worker-dispatch",
                ) as pool:
                    futures = [
                        pool.submit(
                            self._dispatch_claim, claim, worker_prompt_factory,
                            worker_response_decoder, authority_lost=authority_lost
                        )
                        for claim in claims
                    ]
                    dispatch_results = zip(claims, futures)
                    for claim, future in dispatch_results:
                        try:
                            outcome = future.result()
                        except (ControllerRejected, SchedulerError, WorkerFenceError) as exc:
                            blockers.append(f"{claim.task_id}:{str(exc)}")
                            continue
                        except Exception as exc:
                            blockers.append(f"{claim.task_id}:DISPATCH_EXCEPTION:{type(exc).__name__}")
                            continue
                        if outcome.get("status") == "BLOCKED":
                            blockers.append(f"{claim.task_id}:{outcome.get('reason', 'BLOCKED')}")
                        outcomes.append(outcome)
            else:
                for claim in claims:
                    try:
                        outcome = self._dispatch_claim(
                            claim, worker_prompt_factory, worker_response_decoder,
                            authority_lost=authority_lost,
                        )
                    except (ControllerRejected, SchedulerError, WorkerFenceError) as exc:
                        blockers.append(f"{claim.task_id}:{str(exc)}")
                        continue
                    except Exception as exc:
                        blockers.append(f"{claim.task_id}:DISPATCH_EXCEPTION:{type(exc).__name__}")
                        continue
                    if outcome.get("status") == "BLOCKED":
                        blockers.append(f"{claim.task_id}:{outcome.get('reason', 'BLOCKED')}")
                    outcomes.append(outcome)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(0.05, self._master_heartbeat_interval_seconds() * 2.0))
        if heartbeat_errors and not any("MASTER_HEARTBEAT_FAILED" in item for item in blockers):
            blockers.append(f"MASTER_HEARTBEAT_FAILED:{type(heartbeat_errors[0]).__name__}")
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
        worker_response_decoder: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
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
        *,
        authority_lost: Any | None = None,
    ) -> dict[str, Any]:
        self._assert_step_authority(authority_lost)
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
            self._assert_step_authority(authority_lost)
            current = self.gateway.submit_intent(intent_id)
        self._assert_step_authority(authority_lost)
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
        payload = (
            decode_work_result_response(response)
            if worker_response_decoder is None
            else worker_response_decoder(captured)
        )
        if not isinstance(payload, Mapping):
            raise ControllerRejected("WORK_RESULT_DECODER_NOT_MAPPING")
        payload = normalize_worker_result_envelope(payload)
        if str(payload.get("work_result_version") or "") != "1":
            raise ControllerRejected("WORK_RESULT_VERSION_UNSUPPORTED")
        self._assert_step_authority(authority_lost)
        self._validate_preexecution_worker_authority(claim, payload)
        execution_request = payload.get("execution_request")
        # A Worker result that is BLOCKED/PARTIAL/INVALID is evidence of a
        # non-complete attempt, not permission to run a request it happened to
        # include.  Only a COMPLETE result may cross the local execution
        # boundary; otherwise a model could report a blocker and still cause
        # the requested side effect.
        worker_status = str(payload.get("status") or "").strip().upper()
        if execution_request is not None and worker_status == "COMPLETE":
            if self.execution_adapter is None:
                raise ControllerRejected("EXECUTION_ADAPTER_UNAVAILABLE")
            self._assert_step_authority(authority_lost)
            receipt = self._execute_request(claim, execution_request)
            payload["execution_receipt"] = receipt
            payload["result_sha256"] = result_content_sha256(payload)
        elif execution_request is not None:
            # The prompt contract allows a model to include a placeholder
            # digest while reporting a blocker.  Preserve the declaration for
            # audit, but normalize the stored envelope without ever executing
            # the request or trusting the placeholder hash.
            payload["result_sha256"] = result_content_sha256(payload)
        self._assert_step_authority(authority_lost)
        result_id = self.gateway.record_structured_worker_result(claim, payload=payload)
        self._assert_step_authority(authority_lost)
        self.gateway.verify_worker_result(
            result_id,
            result_sha256=str(payload.get("result_sha256") or ""),
        )
        if worker_status != "COMPLETE":
            return {
                "status": "BLOCKED",
                "reason": f"WORKER_RESULT_{worker_status or 'INVALID'}",
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "intent_id": intent_id,
                "intent_state": state,
                "result_id": result_id,
            }
        return {
            "status": "ACCEPTED",
            "assignment_id": claim.assignment_id,
            "task_id": claim.task_id,
            "intent_id": intent_id,
            "intent_state": state,
            "result_id": result_id,
        }

    @staticmethod
    def _validate_preexecution_worker_authority(
        claim: AssignmentClaim, payload: Mapping[str, Any]
    ) -> None:
        """Bind a model claim to the active assignment before any local side effect.

        This gate is intentionally pure: it neither records nor verifies a result,
        and therefore cannot retire an assignment or release its lease before the
        requested action runs.  Final content-hash verification remains after the
        execution receipt is attached.
        """

        exact = (
            ("PROJECT_ID_MISMATCH", payload.get("project_id"), claim.project_id),
            ("ASSIGNMENT_ID_MISMATCH", payload.get("assignment_id"), claim.assignment_id),
            ("TASK_ID_MISMATCH", payload.get("task_id"), claim.task_id),
            ("WORKER_ID_MISMATCH", payload.get("worker_id"), claim.worker_id),
        )
        for error, actual, expected in exact:
            if str(actual or "").strip() != str(expected or "").strip():
                raise ControllerRejected(error)

        objective = str(payload.get("objective_sha256") or "").strip().lower()
        if objective != str(claim.objective_sha256 or "").strip().lower():
            raise ControllerRejected("OBJECTIVE_HASH_MISMATCH")

        observed_version = payload.get("base_state_version")
        if isinstance(observed_version, bool):
            raise ControllerRejected("BASE_STATE_VERSION_INVALID")
        try:
            observed_version = int(observed_version)
        except (TypeError, ValueError) as exc:
            raise ControllerRejected("BASE_STATE_VERSION_INVALID") from exc
        if observed_version != int(claim.base_state_version):
            raise ControllerRejected("BASE_STATE_VERSION_MISMATCH")

        if "slot_id" in payload:
            if str(payload.get("slot_id") or "").strip() != str(claim.slot_id or "").strip():
                raise ControllerRejected("SLOT_ID_MISMATCH")

        context = getattr(claim, "task_context", {}) or {}
        expected_candidate = (
            str(context.get("candidate_commit") or "").strip().lower()
            if isinstance(context, Mapping)
            else ""
        )
        if expected_candidate:
            actual_candidate = str(payload.get("candidate_commit") or "").strip().lower()
            if actual_candidate != expected_candidate:
                raise ControllerRejected("CANDIDATE_COMMIT_MISMATCH")

    def _execute_request(self, claim: AssignmentClaim, request: Any) -> dict[str, Any]:
        """Run one Worker-proposed local action through the bounded adapter."""

        if not isinstance(request, Mapping):
            raise ControllerRejected("EXECUTION_REQUEST_INVALID")
        allowed = {
            "module",
            "args",
            "working_directory",
            "resource_paths",
            "access_mode",
            "timeout_seconds",
            "repository",
            "worktree",
            "base_commit",
        }
        if set(request) - allowed:
            raise ControllerRejected("EXECUTION_REQUEST_UNKNOWN_FIELD")
        args = request.get("args", [])
        resources = request.get("resource_paths", [])
        if not isinstance(args, Sequence) or isinstance(args, (str, bytes)):
            raise ControllerRejected("EXECUTION_REQUEST_ARGS_INVALID")
        if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
            raise ControllerRejected("EXECUTION_REQUEST_RESOURCES_INVALID")
        mode = str(request.get("access_mode") or claim.access_mode).strip().lower()
        if mode != str(claim.access_mode).strip().lower():
            raise ControllerRejected("EXECUTION_REQUEST_ACCESS_MODE_MISMATCH")
        is_write = mode == "write"
        if is_write and self.git_worktree_manager is None:
            raise ControllerRejected("GIT_WORKTREE_MANAGER_UNAVAILABLE")
        if is_write and not all(str(request.get(key) or "").strip() for key in ("repository", "worktree", "base_commit")):
            raise ControllerRejected("GIT_WORKTREE_REQUEST_INCOMPLETE")
        try:
            timeout_seconds = int(request.get("timeout_seconds", 300))
        except (TypeError, ValueError) as exc:
            raise ControllerRejected("EXECUTION_REQUEST_TIMEOUT_INVALID") from exc
        intent_id = f"execution-intent-{claim.assignment_id}"
        intent_payload = {
            "assignment_id": claim.assignment_id,
            "task_id": claim.task_id,
            "master_epoch": claim.master_epoch,
            "lease_token": claim.lease_token,
            "worker_assignment": {
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "worker_id": claim.worker_id,
                "slot_id": claim.slot_id,
                "master_epoch": claim.master_epoch,
                "base_state_version": claim.base_state_version,
                "lease_token": claim.lease_token,
                "resource_scope": list(claim.resource_scope),
                "access_mode": claim.access_mode,
            },
            "request": dict(request),
        }
        get_control = getattr(self.gateway.store, "get_operator_control", None)
        if callable(get_control):
            control = get_control(self.project_id)
            intent_payload["operator_generation"] = int(control["operator_generation"])
            intent_payload["objective_generation"] = int(control["objective_generation"])
        try:
            intent = self.gateway.store.prepare_intent(
                self.project_id,
                intent_id,
                actor_id=claim.worker_id,
                channel=f"execution/{claim.slot_id}",
                action_kind="LOCAL_EXECUTION",
                payload=intent_payload,
            )
        except Exception as exc:
            raise ControllerRejected(f"LOCAL_EXECUTION_INTENT_REJECTED:{exc}") from exc
        intent_state = str(intent.get("state") or "")
        if intent_state == "RESPONSE_CAPTURED":
            try:
                receipt = json.loads(str(intent.get("response_json") or "{}"))
            except (TypeError, ValueError) as exc:
                raise ControllerRejected("LOCAL_EXECUTION_RECEIPT_INVALID") from exc
            if not isinstance(receipt, Mapping):
                raise ControllerRejected("LOCAL_EXECUTION_RECEIPT_INVALID")
            if (
                str(receipt.get("assignment_id") or "") != claim.assignment_id
                or str(receipt.get("task_id") or "") != claim.task_id
            ):
                raise ControllerRejected("LOCAL_EXECUTION_RECEIPT_IDENTITY_MISMATCH")
            try:
                if int(receipt.get("exit_code")) != 0:
                    raise ControllerRejected(f"LOCAL_EXECUTION_FAILED:{receipt.get('exit_code')}")
            except (TypeError, ValueError) as exc:
                raise ControllerRejected("EXECUTION_RECEIPT_EXIT_CODE_INVALID") from exc
            return dict(receipt)
        if intent_state in {"MAY_HAVE_SUBMITTED", "BLOCKED_AMBIGUOUS", "CONFIRMED_SUBMITTED"}:
            raise ControllerRejected("LOCAL_EXECUTION_RECONCILIATION_REQUIRED")
        if intent_state not in {"PREPARED", "VERIFIED_NOT_SUBMITTED"}:
            raise ControllerRejected(f"LOCAL_EXECUTION_INTENT_STATE_INVALID:{intent_state}")
        try:
            self.gateway.store.begin_possible_submit(intent_id)
            assert_generation = getattr(self.gateway.store, "assert_intent_generation", None)
            if callable(assert_generation):
                assert_generation(intent_id)
            worktree_receipt = None
            if is_write:
                worktree_receipt = self.git_worktree_manager.prepare(
                    claim,
                    repository=str(request["repository"]),
                    worktree=str(request["worktree"]),
                    base_commit=str(request["base_commit"]),
                    timeout_seconds=timeout_seconds,
                )
            if callable(assert_generation):
                assert_generation(intent_id)
            receipt = self.execution_adapter.execute(
                claim,
                module=str(request.get("module") or ""),
                args=[str(item) for item in args],
                working_directory=str(request.get("working_directory") or ""),
                resource_paths=[str(item) for item in resources],
                access_mode=mode,
                expected_assignment_id=claim.assignment_id,
                expected_master_epoch=claim.master_epoch,
                expected_lease_token=claim.lease_token,
                timeout_seconds=timeout_seconds,
            )
        except (ExecutionAdapterRejected, GitWorktreeRejected) as exc:
            self.gateway.store.block_intent(
                intent_id,
                reason=f"LOCAL_EXECUTION_REJECTED:{exc}",
                observation={"error": str(exc), "intent_state": "MAY_HAVE_SUBMITTED"},
            )
            raise ControllerRejected(f"LOCAL_EXECUTION_REJECTED:{exc}") from exc
        except Exception as exc:
            self.gateway.store.block_intent(
                intent_id,
                reason="LOCAL_EXECUTION_EXCEPTION",
                observation={"error": type(exc).__name__, "intent_state": "MAY_HAVE_SUBMITTED"},
            )
            raise ControllerRejected("LOCAL_EXECUTION_EXCEPTION") from exc
        raw_exit_code = receipt.exit_code if hasattr(receipt, "exit_code") else (
            receipt.get("exit_code") if isinstance(receipt, Mapping) else None
        )
        try:
            exit_code = int(raw_exit_code)
        except (TypeError, ValueError) as exc:
            self.gateway.store.block_intent(
                intent_id,
                reason="EXECUTION_RECEIPT_EXIT_CODE_INVALID",
                observation={"type": type(raw_exit_code).__name__},
            )
            raise ControllerRejected("EXECUTION_RECEIPT_EXIT_CODE_INVALID") from exc
        if exit_code != 0:
            self.gateway.store.block_intent(
                intent_id,
                reason=f"LOCAL_EXECUTION_FAILED:{exit_code}",
                observation={"exit_code": exit_code},
            )
            raise ControllerRejected(f"LOCAL_EXECUTION_FAILED:{exit_code}")
        if hasattr(receipt, "as_dict"):
            value = receipt.as_dict()
        elif isinstance(receipt, Mapping):
            value = dict(receipt)
        else:
            self.gateway.store.block_intent(
                intent_id,
                reason="EXECUTION_RECEIPT_INVALID",
                observation={"type": type(receipt).__name__},
            )
            raise ControllerRejected("EXECUTION_RECEIPT_INVALID")
        if is_write and worktree_receipt is not None:
            value["git_worktree_receipt"] = (
                worktree_receipt.as_dict()
                if hasattr(worktree_receipt, "as_dict")
                else dict(worktree_receipt)
            )
        if (
            str(value.get("assignment_id") or "") != claim.assignment_id
            or str(value.get("task_id") or "") != claim.task_id
        ):
            self.gateway.store.block_intent(
                intent_id,
                reason="EXECUTION_RECEIPT_IDENTITY_MISMATCH",
                observation={"assignment_id": value.get("assignment_id"), "task_id": value.get("task_id")},
            )
            raise ControllerRejected("EXECUTION_RECEIPT_IDENTITY_MISMATCH")
        try:
            captured = self.gateway.store.capture_local_execution(
                intent_id,
                receipt=value,
                observation={"exit_code": exit_code, "command": value.get("command", [])},
            )
            self.gateway.store.finalize_intent(intent_id)
        except Exception as exc:
            raise ControllerRejected("LOCAL_EXECUTION_CAPTURE_FAILED") from exc
        try:
            return json.loads(str(captured.get("response_json") or "{}"))
        except (TypeError, ValueError) as exc:
            raise ControllerRejected("LOCAL_EXECUTION_RECEIPT_INVALID") from exc

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
            task_context = task.get("task_context", {})
            if not isinstance(task_context, Mapping):
                raise ControllerRejected("TASK_CONTEXT_INVALID")
            try:
                encoded_context = json.dumps(
                    dict(task_context), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            except (TypeError, ValueError) as exc:
                raise ControllerRejected("TASK_CONTEXT_INVALID") from exc
            if len(encoded_context.encode("utf-8")) > 64 * 1024:
                raise ControllerRejected("TASK_CONTEXT_TOO_LARGE")
            task["task_context"] = dict(task_context)
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
