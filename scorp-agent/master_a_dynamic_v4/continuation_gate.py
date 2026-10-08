"""Deterministic, model-free continuation candidate planner.

This is the missing *policy seam*, not a browser automation transport. It
uses ActivationArbiter actions and host-observed session state to propose a
durable, deduplicatable continuation candidate. It never sends or authorizes
a ChatGPT message. Callers must persist the idempotency key atomically and
use the existing fenced, explicitly authorized browser adapter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Collection

from .session_admission import AdmissionPolicy, SessionObservation, evaluate_session


_MASTER_ACTIONS = frozenset({"WAKE_MASTER", "REASON_MASTER", "RESUME_MASTER"})
_WORKER_ACTIONS = frozenset({"ASSIGN_WORKER", "RESUME_WORKER"})
_WAIT_ACTIONS = frozenset({
    "HEARTBEAT_IDLE", "VERIFY_MASTER", "RECONCILE_AMBIGUOUS",
    "RECONCILE_SUBMITTED", "FENCE_STALE_RESULTS", "RECOVER_STALLED",
})
_STOP_ACTIONS = frozenset({"TERMINAL", "EMERGENCY_STOP", "BLOCKED"})


@dataclass(frozen=True)
class ContinuationRequest:
    project_id: str
    decision_id: str
    decision_action: str
    state_version: int
    master_epoch: int
    daemon_epoch: int


@dataclass(frozen=True)
class ContinuationDecision:
    status: str
    reason: str
    idempotency_key: str | None
    browser_send_authorized: bool = False


def plan_continuation(
    request: ContinuationRequest,
    observation: SessionObservation,
    policy: AdmissionPolicy,
    *,
    already_queued: Collection[str] = (),
) -> ContinuationDecision:
    """Compose only an observable continuation candidate, with no external I/O.

    `already_queued` must come from the authoritative durable store. A
    caller must transactionally persist the key before approaching browser I/O.
    A request cannot grant dispatch rights by placing a model name in any field.
    """
    if not isinstance(request, ContinuationRequest):
        raise TypeError("CONTINUATION_REQUEST_REQUIRED")

    def decision(status: str, reason: str, key: str | None = None) -> ContinuationDecision:
        return ContinuationDecision(status, reason, key)

    if not request.project_id.strip() or not request.decision_id.startswith("decision-"):
        return decision("BLOCKED", "ARBITER_IDENTITY_INVALID")
    for field in (request.state_version, request.master_epoch, request.daemon_epoch):
        if type(field) is not int or field < 0:
            return decision("BLOCKED", "EPOCH_OR_STATE_VERSION_INVALID")
    action = request.decision_action
    if action in _STOP_ACTIONS:
        return decision("STOP", "ARBITER_" + action)
    if action in _WAIT_ACTIONS:
        return decision("OBSERVE_ONLY", "ARBITER_" + action)
    if action not in _MASTER_ACTIONS | _WORKER_ACTIONS:
        return decision("BLOCKED", "ARBITER_ACTION_UNRECOGNIZED")
    expected_role = "MASTER" if action in _MASTER_ACTIONS else "WORKER"
    if policy.required_role != expected_role:
        return decision("BLOCKED", "ARBITER_ROLE_MISMATCH")
    candidate = evaluate_session(observation, policy)
    if candidate.status != "ELIGIBLE_FOR_SCHEDULING":
        return decision("BLOCKED", candidate.reason)

    material = {
        "protocol_version": "scorp.continuation-candidate/1",
        "project_id": request.project_id,
        "decision_id": request.decision_id,
        "decision_action": request.decision_action,
        "state_version": request.state_version,
        "master_epoch": request.master_epoch,
        "daemon_epoch": request.daemon_epoch,
        "physical_session_id": observation.session_id,
        "conversation_url": observation.conversation_url,
        "binding_generation": observation.generation,
    }
    key = "continue-" + hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if key in already_queued:
        return decision("ALREADY_QUEUED", "IDEMPOTENCY_KEY_EXISTS", key)
    return decision("READY_FOR_GATED_ADAPTER", "OBSERVATION_VERIFIED", key)
