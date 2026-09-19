"""Deterministic activation decisions for the local SCORP daemon.

The arbiter is deliberately side-effect free.  It consumes a durable snapshot
and returns one auditable action; the daemon is responsible for persisting the
decision and executing the action through the existing fenced adapters.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping


@dataclasses.dataclass(frozen=True)
class ArbiterSnapshot:
    project_id: str
    project_status: str
    master_epoch: int
    daemon_epoch: int
    master_active: bool
    active_workers: int
    free_slots: int
    ready_tasks: int
    ambiguous_intents: int
    progress_state: str = "IDLE"
    stale_results: int = 0
    auth_blocked: bool = False
    operator_state: str = "RUNNING"
    auth_host_blocker: str | None = None
    pending_results: int = 0
    active_worker_lost: bool = False
    browser_semantic_state: str = "UNKNOWN"
    content_changed: bool = False
    progress_made: bool = False
    browser_succeeded: bool = False
    browser_error: bool = False
    reasoning_required: bool = False

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ActivationDecision:
    decision_id: str
    project_id: str
    actor_id: str
    daemon_epoch: int
    master_epoch: int
    action: str
    reason: str
    capacity: int = 0
    input_sha256: str = ""

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


class ActivationArbiter:
    """Choose exactly one next lifecycle action using fixed priority.

    Priority is intentionally fail-closed: terminal and authentication
    blockers stop scheduling; ambiguous browser effects are reconciled before
    any new Master or Worker action; only then can work be assigned.
    """

    def __init__(self, *, actor_id: str = "scorp-daemon") -> None:
        self.actor_id = str(actor_id or "").strip()
        if not self.actor_id:
            raise ValueError("ARBITER_ACTOR_REQUIRED")

    def decide(self, snapshot: ArbiterSnapshot | Mapping[str, object]) -> ActivationDecision:
        if isinstance(snapshot, ArbiterSnapshot):
            value = snapshot
        elif isinstance(snapshot, Mapping):
            value = ArbiterSnapshot(**dict(snapshot))
        else:
            raise TypeError("ARBITER_SNAPSHOT_INVALID")
        if not value.project_id:
            raise ValueError("PROJECT_ID_EMPTY")
        if value.master_epoch < 0 or value.daemon_epoch < 0:
            raise ValueError("EPOCH_INVALID")
        if min(value.active_workers, value.free_slots, value.ready_tasks, value.ambiguous_intents, value.stale_results, value.pending_results) < 0:
            raise ValueError("SNAPSHOT_COUNT_INVALID")

        raw = json.dumps(value.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        input_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        action = "HEARTBEAT_IDLE"
        reason = "NO_READY_WORK"
        capacity = 0

        if value.project_status in {"COMPLETE", "HARD_BLOCKED", "TERMINAL"}:
            action, reason = "TERMINAL", "PROJECT_TERMINAL"
        elif str(value.operator_state) == "EMERGENCY_STOPPED":
            action, reason = "EMERGENCY_STOP", "OPERATOR_EMERGENCY_STOPPED"
        elif str(value.operator_state) not in {"", "ACTIVE", "RUNNING"}:
            action, reason = "BLOCKED", f"OPERATOR_FENCE_{str(value.operator_state).upper()}"
        elif value.auth_blocked or value.auth_host_blocker:
            action, reason = "BLOCKED", str(value.auth_host_blocker or "AUTHENTICATION_REQUIRED")
        elif value.ambiguous_intents:
            action, reason = "RECONCILE_AMBIGUOUS", "AMBIGUOUS_BROWSER_SIDE_EFFECT"
        elif value.stale_results:
            action, reason = "FENCE_STALE_RESULTS", "STALE_RESULT_REQUIRES_FENCING"
        elif not value.master_active:
            action, reason = "RESUME_MASTER", "MASTER_LEASE_MISSING"
        elif value.pending_results:
            action, reason = "WAKE_MASTER", "PENDING_RESULT_REQUIRES_MASTER_WAKE"
        elif value.active_worker_lost:
            action, reason = "RESUME_WORKER", "WORKER_LEASE_LOST"
        elif value.progress_state == "STALLED_CONFIRMED":
            action, reason = "RECOVER_STALLED", "PROGRESS_STALLED"
        elif value.ready_tasks and value.free_slots:
            action, reason = "ASSIGN_WORKER", "READY_TASKS_AND_FREE_SLOT"
            capacity = min(value.ready_tasks, value.free_slots)
        elif value.reasoning_required and value.active_workers == 0:
            action, reason = "REASON_MASTER", "DURABLE_STATE_REQUIRES_MASTER_REASONING"

        decision_material = {
            "project_id": value.project_id,
            "daemon_epoch": value.daemon_epoch,
            "master_epoch": value.master_epoch,
            "action": action,
            "reason": reason,
            "input_sha256": input_sha,
        }
        decision_id = "decision-" + hashlib.sha256(
            json.dumps(decision_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]
        return ActivationDecision(
            decision_id=decision_id,
            project_id=value.project_id,
            actor_id=self.actor_id,
            daemon_epoch=value.daemon_epoch,
            master_epoch=value.master_epoch,
            action=action,
            reason=reason,
            capacity=capacity,
            input_sha256=input_sha,
        )
