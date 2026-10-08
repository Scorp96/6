"""Deterministic, side-effect-free shortlist across GPT conversation sessions 1..N.

This module does not increase V4's two-Worker concurrency limit. The set of
known sessions may be arbitrarily larger than the set of running assignments.
All bindings must come from the authoritative host side, not a GPT reply.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .session_admission import AdmissionPolicy, SessionObservation, evaluate_session


@dataclass(frozen=True)
class HostBinding:
    conversation_url: str
    generation: int


@dataclass(frozen=True)
class SelectionResult:
    selected_session_ids: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]
    browser_send_authorized: bool = False


def shortlist_workers(
    observations: Sequence[SessionObservation],
    bindings: Mapping[str, HostBinding],
    policy: AdmissionPolicy,
) -> SelectionResult:
    """List eligible sessions without claiming leases, sending, or writing.

    Candidate metadata must never be used to fabricate a physical binding.
    Evaluation uses the independently provided, host-side URL and generation.
    Model names are irrelevant unless policy.required_model explicitly binds
    a host-verified model requirement.
    """
    if not isinstance(policy, AdmissionPolicy):
        raise TypeError("SESSION_POLICY_REQUIRED")
    if policy.required_role != "WORKER":
        raise ValueError("SHORTLIST_WORKER_ROLE_REQUIRED")
    if type(policy.max_workers) is not int or policy.max_workers < 1 or policy.max_workers > 2:
        raise ValueError("V4_WORKER_CAPACITY_INVALID")
    if type(policy.active_workers) is not int or policy.active_workers < 0:
        raise ValueError("ACTIVE_WORKER_COUNT_INVALID")

    by_id: dict[str, list[SessionObservation]] = {}
    for observed in observations:
        if not isinstance(observed, SessionObservation):
            raise TypeError("SESSION_OBSERVATION_REQUIRED")
        by_id.setdefault(observed.session_id, []).append(observed)
    free = max(0, policy.max_workers - policy.active_workers)
    selected: list[str] = []
    skipped: list[tuple[str, str]] = []
    used_conversations: set[str] = set()

    for session_id in sorted(by_id):
        records = by_id[session_id]
        if not session_id.strip():
            skipped.append((session_id, "SESSION_ID_MISSING"))
            continue
        if len(records) != 1:
            skipped.append((session_id, "DUPLICATE_SESSION_ID"))
            continue
        bound = bindings.get(session_id)
        if not isinstance(bound, HostBinding):
            skipped.append((session_id, "HOST_BINDING_MISSING"))
            continue
        if bound.conversation_url in used_conversations:
            skipped.append((session_id, "DUPLICATE_PHYSICAL_CONVERSATION"))
            continue
        observed = records[0]
        rules = replace(
            policy,
            expected_conversation_url=bound.conversation_url,
            expected_generation=bound.generation,
        )
        result = evaluate_session(observed, rules)
        if result.status != "ELIGIBLE_FOR_SCHEDULING":
            skipped.append((session_id, result.reason))
            continue
        if free == 0:
            skipped.append((session_id, "WORKER_CAPACITY_EXHAUSTED"))
            continue
        selected.append(session_id)
        used_conversations.add(bound.conversation_url)
        free -= 1
    return SelectionResult(tuple(selected), tuple(skipped))
