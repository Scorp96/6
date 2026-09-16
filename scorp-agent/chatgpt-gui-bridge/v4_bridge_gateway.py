"""Explicit candidate entry point joining the GUI bridge to V4 state.

This module is intentionally opt-in. The legacy JSON relay remains available for
compatibility, but it is not allowed to become the V4 authority by accident.
"""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from master_a_dynamic_v4.browser_adapter import BrowserAdapter
from master_a_dynamic_v4.acceptance import AcceptanceDecision, AcceptanceValidator
from master_a_dynamic_v4.models import CommitResult
from master_a_dynamic_v4.master_watchdog import MasterWatchdog
from master_a_dynamic_v4.path_policy import PathPolicy
from master_a_dynamic_v4.recovery import recover_pending_intents
from master_a_dynamic_v4.scheduler import AssignmentClaim, Scheduler, SchedulerError, WorkerFenceError
from master_a_dynamic_v4.state_store import StateStore, utc_now


DEFAULT_QUEUE_REPO = "Scorp96/666"
LEGACY_CONTROL_REPO = "Scorp96/scorp-control-plane"


class QueueConfig:
    def __init__(self, repo: str, *, migration_mode: bool = False):
        value = str(repo or "").strip()
        if not value or "/" not in value or value.count("/") != 1:
            raise ValueError("QUEUE_REPO_INVALID")
        if value == LEGACY_CONTROL_REPO and not migration_mode:
            raise ValueError("LEGACY_CONTROL_REPO_REQUIRES_MIGRATION_MODE")
        self.repo = value
        self.migration_mode = bool(migration_mode)

    @classmethod
    def for_repo(cls, repo: str | None = None, *, migration_mode: bool = False) -> "QueueConfig":
        return cls(repo or DEFAULT_QUEUE_REPO, migration_mode=migration_mode)


class V4BridgeGateway:
    """Small, deterministic seam used by a future bridge runtime.

    The gateway owns the SQLite store and delegates all browser side effects to
    ``BrowserAdapter``. Callers must provide an engine implementing
    ``auth_state``, ``submit`` and ``reconcile``; no implicit login bypass or
    browser discovery is performed here.
    """

    def __init__(
        self,
        database_path: str | pathlib.Path,
        project_id: str,
        allowed_roots: Sequence[str | pathlib.Path],
        engine: Any,
        *,
        protected_roots: Sequence[str | pathlib.Path] = (),
        master_ttl_seconds: int = 1500,
    ):
        self.store = StateStore(database_path, allowed_roots=allowed_roots)
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            self.store.close()
            raise ValueError("PROJECT_ID_EMPTY")
        self.scheduler = Scheduler(
            self.store,
            self.project_id,
            PathPolicy(allowed_roots, protected_roots=protected_roots),
            max_workers=2,
        )
        self.adapter = BrowserAdapter(self.store, engine)
        self.master_watchdog = MasterWatchdog(
            self.store, self.project_id, ttl_seconds=int(master_ttl_seconds)
        )

    def close(self) -> None:
        self.store.close()

    def ensure_contract(
        self,
        root_contract: Mapping[str, Any],
        acceptance_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.store.create_contract(
            self.project_id,
            root_contract=root_contract,
            acceptance_contract=acceptance_contract,
        )

    def enqueue_graph(self, tasks: Sequence[Mapping[str, Any]]) -> None:
        self.scheduler.enqueue_graph(tasks)

    def acquire_master_epoch(self, *, expected_epoch: int) -> int:
        """Fence a new logical Master A incarnation in the transaction core."""
        return self.store.advance_master_epoch(
            self.project_id, expected_epoch=int(expected_epoch)
        )

    def start_master_session(self, session_id: str, *, now=None) -> dict[str, Any]:
        """Start or resume one physical Master A session under a durable lease."""
        return self.master_watchdog.start(session_id, now=now)

    def heartbeat_master_session(
        self, session_id: str, *, master_epoch: int, now=None
    ) -> dict[str, Any]:
        return self.master_watchdog.heartbeat(
            session_id, master_epoch=int(master_epoch), now=now
        )

    def end_master_session(
        self, session_id: str, *, master_epoch: int, reason: str, now=None
    ) -> dict[str, Any]:
        return self.master_watchdog.end(
            session_id,
            master_epoch=int(master_epoch),
            reason=reason,
            now=now,
        )

    def watchdog_once(self, *, now=None) -> dict[str, Any]:
        """Return the fail-closed Master A liveness decision."""
        return self.master_watchdog.run_once(now=now)

    def commit_master_proposal(
        self,
        transition_id: str,
        proposal: Mapping[str, Any],
        *,
        evidence_refs: Sequence[str] = (),
        expected_version: int | None = None,
        master_epoch: int | None = None,
    ) -> CommitResult:
        """Submit a validated Master A proposal through StateStore's CAS gate.

        Callers should pass the state version and epoch they observed. Omitting
        either uses the current snapshot for convenience, while explicit stale
        values remain fenced and cannot overwrite newer state.
        """
        state = self.store.get_project_state(self.project_id)
        version = int(state["state_version"]) if expected_version is None else int(expected_version)
        epoch = int(state["master_epoch"]) if master_epoch is None else int(master_epoch)
        return self.store.commit(
            version,
            epoch,
            transition_id,
            proposal,
            evidence_refs,
        )

    def commit_master_proposal_and_enqueue(
        self,
        transition_id: str,
        proposal: Mapping[str, Any],
        tasks: Sequence[Mapping[str, Any]],
        *,
        evidence_refs: Sequence[str] = (),
        expected_version: int | None = None,
        master_epoch: int | None = None,
    ) -> CommitResult:
        """Atomically admit a Master proposal and materialize its task graph."""
        normalized_graph = self.scheduler.prepare_graph(tasks)
        state = self.store.get_project_state(self.project_id)
        version = int(state["state_version"]) if expected_version is None else int(expected_version)
        epoch = int(state["master_epoch"]) if master_epoch is None else int(master_epoch)
        return self.store.commit(
            version,
            epoch,
            transition_id,
            proposal,
            evidence_refs,
            graph_rows=normalized_graph,
        )

    def evaluate_completion(
        self,
        *,
        candidate_commit: str,
        artifact_hashes: Mapping[str, str],
    ) -> AcceptanceDecision:
        """Run the independent completion gate without mutating project state."""
        return AcceptanceValidator(self.store).evaluate(
            self.project_id,
            candidate_commit,
            artifact_hashes,
        )

    def claim_workers(self, *, master_epoch: int = 0, limit: int = 2, now=None):
        """Atomically claim up to two runnable dynamic Worker assignments."""
        if int(limit) > 2:
            raise SchedulerError("V4_WORKER_LIMIT_INVALID")
        return self.scheduler.claim_runnable(master_epoch=master_epoch, limit=limit, now=now)

    def load_worker_claims(self, *, master_epoch: int, now=None):
        """Rehydrate active durable Worker assignments after a restart."""
        return self.scheduler.load_active_claims(master_epoch=int(master_epoch), now=now)

    def renew_worker_lease(self, claim, *, master_epoch=None, lease_token=None, now=None, lease_seconds=900):
        """Renew one assignment lease through the scheduler fencing gate."""
        return self.scheduler.renew_worker_lease(
            claim,
            master_epoch=master_epoch,
            lease_token=lease_token,
            now=now,
            lease_seconds=lease_seconds,
        )

    def record_worker_result(self, claim, *, kind: str, payload: Mapping[str, Any], now=None) -> str:
        return self.scheduler.record_candidate(
            claim.assignment_id,
            lease_token=claim.lease_token,
            master_epoch=claim.master_epoch,
            kind=kind,
            payload=payload,
            now=now,
        )

    def record_structured_worker_result(self, claim, *, payload: Mapping[str, Any], now=None) -> str:
        """Admit a version-bound WorkResult through the V4 scheduler."""
        return self.scheduler.record_work_result(claim, payload=payload, now=now)

    def prepare_worker_intent(
        self,
        claim: AssignmentClaim,
        prompt: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one assignment-bound prompt for a distinct Worker channel.

        The intent is durable and idempotent. A real browser engine receives a
        worker channel with no pre-bound URL, so it may create one physical
        ChatGPT conversation for that assignment. The lease and epoch are
        checked before the intent is prepared; a stale claim cannot open a new
        browser side effect.
        """
        if not isinstance(claim, AssignmentClaim) or claim.project_id != self.project_id:
            raise WorkerFenceError("ASSIGNMENT_CLAIM_INVALID")
        text = str(prompt or "")
        if not text:
            raise ValueError("PROMPT_EMPTY")
        with self.store._connection() as conn:
            row = conn.execute(
                """
                SELECT a.*,l.state AS lease_state,l.expires_at,s.master_epoch AS current_epoch
                FROM assignments a
                JOIN leases l ON l.assignment_id=a.assignment_id
                JOIN project_state s ON s.project_id=a.project_id
                WHERE a.assignment_id=? AND a.project_id=?
                """,
                (claim.assignment_id, self.project_id),
            ).fetchone()
        if row is None:
            raise WorkerFenceError("ASSIGNMENT_NOT_FOUND")
        if (
            str(row["lease_token"]) != str(claim.lease_token)
            or str(row["lease_state"]) != "ACTIVE"
            or str(row["state"]) != "ACTIVE"
            or int(row["master_epoch"]) != int(claim.master_epoch)
            or int(row["current_epoch"]) != int(claim.master_epoch)
            or str(row["expires_at"]) <= utc_now()
        ):
            raise WorkerFenceError("WORKER_FENCED")
        prompt_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload: dict[str, Any] = {
            "prompt": text,
            "prompt_sha256": prompt_sha256,
            "worker_assignment": {
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "worker_id": claim.worker_id,
                "slot_id": claim.slot_id,
                "master_epoch": claim.master_epoch,
                "base_state_version": claim.base_state_version,
                "lease_token": claim.lease_token,
                "expires_at": claim.expires_at,
                "objective_sha256": claim.objective_sha256,
                "resource_scope": list(claim.resource_scope),
                "access_mode": claim.access_mode,
            },
            "required_response": "WORK_RESULT/1",
        }
        get_control = getattr(self.store, "get_operator_control", None)
        if callable(get_control):
            control = get_control(self.project_id)
            payload["operator_generation"] = int(control["operator_generation"])
            payload["objective_generation"] = int(control["objective_generation"])
        if metadata:
            payload["metadata"] = dict(metadata)
        return self.store.prepare_intent(
            self.project_id,
            f"worker-intent-{claim.assignment_id}",
            actor_id=claim.worker_id,
            channel=f"worker/{claim.slot_id}",
            action_kind="CHATGPT_WORKER_SUBMIT",
            payload=payload,
        )

    def submit_worker_intent(
        self,
        claim: AssignmentClaim,
        prompt: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        intent = self.prepare_worker_intent(claim, prompt, metadata=metadata)
        return self.submit_intent(str(intent["intent_id"]))

    def verify_worker_result(self, result_id: str, *, result_sha256: str, now=None) -> None:
        self.scheduler.verify_candidate(result_id, result_sha256=result_sha256, now=now)

    def prepare_browser_intent(
        self,
        intent_id: str,
        prompt: str,
        *,
        actor_id: str = "A",
        channel: str = "master",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = str(prompt or "")
        if not text:
            raise ValueError("PROMPT_EMPTY")
        prompt_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload: dict[str, Any] = {
            "prompt": text,
            "prompt_sha256": prompt_sha256,
        }
        get_control = getattr(self.store, "get_operator_control", None)
        if callable(get_control):
            control = get_control(self.project_id)
            payload["operator_generation"] = int(control["operator_generation"])
            payload["objective_generation"] = int(control["objective_generation"])
        if metadata:
            payload["metadata"] = dict(metadata)
        return self.store.prepare_intent(
            self.project_id,
            intent_id,
            actor_id=actor_id,
            channel=channel,
            action_kind="CHATGPT_SUBMIT",
            payload=payload,
        )

    def submit_intent(self, intent_id: str) -> dict[str, Any]:
        return self.adapter.submit_once(intent_id)

    def recover(self) -> list[tuple[str, str]]:
        return recover_pending_intents(self.adapter)

    def describe(self) -> dict[str, Any]:
        state = self.store.get_project_state(self.project_id)
        return {
            "project_id": self.project_id,
            "master_epoch": int(state["master_epoch"]),
            "state_version": int(state["state_version"]),
            "status": str(state["status"]),
            "pending_intents": len(self.store.pending_intents()),
            "queue_authority": "sqlite",
            "legacy_json_role": "import_or_read_only_compatibility",
            "master_session_authority": "sqlite.master_sessions",
        }
