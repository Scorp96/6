"""Read-only integration: host turn evidence -> V4 continuation candidate.

Do not trust a caller-provided SessionObservation.response_status. This
adapter independently derives it from fresh two-sample, host-attested turn
completion evidence, then calls the existing no-send continuation planner.
There is no local execution or browser submission path in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Collection, Sequence

from .continuation_gate import (
    ContinuationDecision, ContinuationRequest, plan_continuation,
)
from .session_admission import AdmissionPolicy, SessionObservation
from .turn_completion_evidence import TurnSample, assess_turn_completion
from .host_terminal_receipt import HostTerminalReceipt


@dataclass(frozen=True)
class VerifiedContinuationResult:
    status: str
    reason: str
    idempotency_key: str | None = None
    completion_proof_sha256: str | None = None
    browser_send_authorized: bool = False


def plan_with_verified_turn(
    request: ContinuationRequest,
    observation: SessionObservation,
    policy: AdmissionPolicy,
    samples: Sequence[TurnSample],
    *,
    expected_intent_id: str,
    now_monotonic_ms: int,
    maximum_observation_age_ms: int = 30000,
    already_queued: Collection[str] | None = None,
    host_receipts: Sequence[HostTerminalReceipt] | None = None,
    host_attestation_key: bytes | None = None,
) -> VerifiedContinuationResult:
    """Compute a no-send candidate; unknown or stale evidence always blocks."""
    def blocked(reason: str) -> VerifiedContinuationResult:
        return VerifiedContinuationResult("BLOCKED", reason)

    if not isinstance(request, ContinuationRequest):
        raise TypeError("CONTINUATION_REQUEST_REQUIRED")
    # Terminal, emergency and observation-only decisions must never depend
    # on availability of a browser or a fresh GPT output. They require no
    # wake-up, consume zero model tokens, and retain the arbiter's priority.
    if request.decision_action in {
        "TERMINAL", "EMERGENCY_STOP", "BLOCKED",
        "HEARTBEAT_IDLE", "VERIFY_MASTER", "RECONCILE_AMBIGUOUS",
        "RECONCILE_SUBMITTED", "FENCE_STALE_RESULTS", "RECOVER_STALLED",
    }:
        stopped = plan_continuation(
            request, observation, policy,
            already_queued=already_queued if already_queued is not None else (),
        )
        return VerifiedContinuationResult(
            stopped.status, stopped.reason, stopped.idempotency_key,
        )

    # A resumable candidate must never infer an empty durable queue from a
    # missing caller argument. Production must supply its authoritative
    # persisted idempotency-key snapshot (and atomically insert before I/O).
    if already_queued is None or isinstance(already_queued,(str,bytes)):
        return blocked("IDEMPOTENCY_LEDGER_UNVERIFIED")

    if (
        type(now_monotonic_ms) is not int or now_monotonic_ms < 0
        or type(maximum_observation_age_ms) is not int
        or maximum_observation_age_ms < 3000
    ):
        return blocked("HOST_CLOCK_INVALID")
    if not samples or not isinstance(samples[-1], TurnSample):
        return blocked("HOST_OBSERVATIONS_MISSING")
    last_ms = samples[-1].sampled_at_ms
    if type(last_ms) is not int or last_ms > now_monotonic_ms:
        return blocked("HOST_OBSERVATION_IN_FUTURE")
    if now_monotonic_ms - last_ms > maximum_observation_age_ms:
        return blocked("HOST_OBSERVATION_STALE")

    state = assess_turn_completion(
        samples,
        expected_session_id=observation.session_id,
        expected_conversation_url=policy.expected_conversation_url,
        expected_binding_generation=policy.expected_generation,
        expected_intent_id=expected_intent_id,
        maximum_separation_ms=maximum_observation_age_ms,
        host_receipts=host_receipts,
        host_attestation_key=host_attestation_key,
    )
    if state.status != "IDLE_CONFIRMED":
        return blocked("TURN_NOT_COMPLETED:" + state.reason)
    verified_observation = replace(observation, response_status=state.status)
    decision: ContinuationDecision = plan_continuation(
        request, verified_observation, policy, already_queued=already_queued,
    )
    return VerifiedContinuationResult(
        status=decision.status,
        reason=decision.reason,
        idempotency_key=decision.idempotency_key,
        completion_proof_sha256=state.proof,
    )
