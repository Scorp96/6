"""Fail-closed, model-free completion evidence for one ChatGPT turn.

This module does not navigate or send. It only combines observations supplied
by a trusted, read-only host adapter. UI snapshots being visually stable or
lacking a "Stop generating" button are not positive completion proof.

The only admitted positive terminal signal is a HOST_VERIFIED terminal event,
bound to the exact turn/intent, physical conversation and model-independent
lease generation. The current Chrome Use driver does NOT yet provide that
signal; until a compatible host adapter exists, callers must return UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .session_admission import _canonical_conversation_url


@dataclass(frozen=True)
class TurnSample:
    session_id: str
    conversation_url: str
    binding_generation: int
    intent_id: str
    sampled_at_ms: int
    generating: bool | None
    tool_pending: bool | None
    response_sha256: str | None = None
    response_intent_verified: bool = False
    finish_event: str | None = None
    finish_event_provenance: str | None = None


@dataclass(frozen=True)
class CompletionResult:
    status: str
    reason: str
    proof: str | None = None
    browser_send_authorized: bool = False


def assess_turn_completion(
    samples: Sequence[TurnSample],
    *,
    expected_session_id: str,
    expected_conversation_url: str,
    expected_binding_generation: int,
    expected_intent_id: str,
    minimum_separation_ms: int = 3000,
    maximum_separation_ms: int = 30000,
) -> CompletionResult:
    """Return IDLE_CONFIRMED only after two scoped, affirmative host proofs.

    Sample times are used only for ordering/debounce; the host must supply
    them from its monotonic clock. This routine does not authenticate the
    sample producer, and does not grant browser or local execution authority.
    """
    def unknown(reason: str) -> CompletionResult:
        return CompletionResult("UNKNOWN", reason)

    if (
        not isinstance(expected_session_id, str) or not expected_session_id.strip()
        or not isinstance(expected_intent_id, str) or not expected_intent_id.strip()
        or type(expected_binding_generation) is not int
        or expected_binding_generation < 0
        or type(minimum_separation_ms) is not int
        or minimum_separation_ms < 1000
        or type(maximum_separation_ms) is not int
        or maximum_separation_ms < minimum_separation_ms
    ):
        return unknown("EXPECTED_BINDING_INVALID")
    canonical_url = _canonical_conversation_url(expected_conversation_url)
    if not canonical_url:
        return unknown("EXPECTED_CONVERSATION_URL_INVALID")
    if not isinstance(samples, Sequence) or len(samples) < 2:
        return unknown("TWO_FRESH_OBSERVATIONS_REQUIRED")
    first, last = samples[-2], samples[-1]
    if not isinstance(first, TurnSample) or not isinstance(last, TurnSample):
        return unknown("HOST_SAMPLE_INVALID")

    if type(first.sampled_at_ms) is not int or type(last.sampled_at_ms) is not int:
        return unknown("HOST_SAMPLE_TIME_INVALID")
    if (
        first.sampled_at_ms < 0
        or last.sampled_at_ms <= first.sampled_at_ms
        or last.sampled_at_ms - first.sampled_at_ms < minimum_separation_ms
    ):
        return unknown("OBSERVATION_STABILITY_WINDOW_NOT_MET")
    if last.sampled_at_ms - first.sampled_at_ms > maximum_separation_ms:
        return unknown("OBSERVATION_PAIR_TOO_OLD")

    for sample in (first, last):
        if sample.session_id != expected_session_id:
            return unknown("PHYSICAL_SESSION_MISMATCH")
        if _canonical_conversation_url(sample.conversation_url) != canonical_url:
            return unknown("PHYSICAL_CONVERSATION_MISMATCH")
        if (
            type(sample.binding_generation) is not int
            or sample.binding_generation != expected_binding_generation
        ):
            return unknown("BINDING_GENERATION_MISMATCH")
        if sample.intent_id != expected_intent_id:
            return unknown("TURN_INTENT_MISMATCH")
        if sample.generating is True:
            return CompletionResult("GENERATING", "HOST_REPORTS_GENERATING")
        if sample.generating is not False or sample.tool_pending is not False:
            return unknown("HOST_PROGRESS_STATE_UNVERIFIED")
        if not sample.response_intent_verified:
            return unknown("RESPONSE_INTENT_NOT_VERIFIED")
        value = str(sample.response_sha256 or "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            return unknown("STRUCTURED_RESPONSE_DIGEST_UNVERIFIED")
        if sample.finish_event != "TURN_FINAL_CONFIRMED":
            return unknown("HOST_TERMINAL_EVENT_MISSING")
        if sample.finish_event_provenance != "HOST_VERIFIED":
            return unknown("HOST_TERMINAL_PROVENANCE_UNVERIFIED")

    if first.response_sha256 != last.response_sha256:
        return unknown("RESPONSE_CHANGED_BETWEEN_OBSERVATIONS")
    return CompletionResult(
        "IDLE_CONFIRMED",
        "TWO_STABLE_HOST_TERMINAL_PROOFS",
        proof=str(last.response_sha256),
    )
