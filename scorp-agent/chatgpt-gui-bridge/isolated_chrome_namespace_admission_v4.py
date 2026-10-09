"""Opt-in, isolated Chrome Use namespace admission; NEVER authorize send.

Native Chrome Use `session resume` may return success while no new session
appears (observed on the user's Windows 2026-10-09). An exit code/ok flag
alone MUST NOT authorize a new tab or adoption of any existing Master tab.

This is the *pre-tab* stage. It doesn't create tabs, read conversations,
navigate, authenticate, submit prompts or authorize any browser action.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_CANARY = re.compile(r"^scorp-r2-isolated-[a-z0-9-]{8,55}$")


@dataclass(frozen=True)
class IsolatedNamespaceAdmission:
    status: str
    reason: str
    before_session_count: int | None = None
    after_session_count: int | None = None
    resume_command_accepted: bool = False
    newly_observed_namespace: bool = False
    browser_send_authorized: bool = False
    tab_navigation_authorized: bool = False
    physical_isolation_attested: bool = False
    model_calls: int = 0


def _sessions(response: object) -> set[str] | None:
    if not isinstance(response, dict) or response.get("ok") is not True:
        return None
    values = response.get("sessions")
    if not isinstance(values, list) or len(values) > 256:
        return None
    found = []
    for row in values:
        if not isinstance(row, dict):
            return None
        name = row.get("name")
        if not isinstance(name, str) or not name or len(name) > 128:
            return None
        found.append(name)
    if len(found) != len(set(found)):
        return None
    return set(found)


async def attempt_isolated_namespace_resume_without_tabs(
    cli: Any,
    *,
    namespace: str,
    timeout_seconds: int = 10,
) -> IsolatedNamespaceAdmission:
    """Attempt only `session resume` for an absent namespaced candidate.

    Does not treat a new name as ownership evidence. A separate host
    lifecycle/ownership proof will be required before any tab operation.
    """
    def denied(reason: str, *, state: str = "BLOCKED",
               before: int | None = None, after: int | None = None,
               accepted: bool = False, newly: bool = False):
        return IsolatedNamespaceAdmission(
            state, reason, before, after, accepted, newly,
        )

    if (
        not isinstance(namespace, str)
        or _CANARY.fullmatch(namespace) is None
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 30
    ):
        return denied("ISOLATED_NAMESPACE_OR_TIMEOUT_INVALID")

    list_sessions = getattr(cli, "list_sessions_readonly", None)
    resume = getattr(cli, "run_json", None)
    if not callable(list_sessions) or not callable(resume):
        return denied("ISOLATED_CHROME_CAPABILITY_UNAVAILABLE")

    try:
        before = _sessions(
            await list_sessions(timeout_seconds=timeout_seconds),
        )
    except Exception:
        return denied("PRE_RESUME_INVENTORY_UNAVAILABLE")
    if before is None:
        return denied("PRE_RESUME_INVENTORY_INVALID")
    if namespace in before:
        return denied("ISOLATED_NAMESPACE_ALREADY_EXISTS", before=len(before))

    try:
        answer = await resume(
            namespace, "session", "resume", timeout_seconds=timeout_seconds,
        )
    except Exception:
        # Timeout/failure may have performed a side effect. Never retry here
        # and never claim absence based on a process-level exception.
        return denied("RESUME_EFFECT_UNKNOWN", before=len(before))
    accepted = (
        isinstance(answer, dict)
        and (answer.get("ok") is True or answer.get("success") is True)
    )
    try:
        after = _sessions(await list_sessions(timeout_seconds=timeout_seconds))
    except Exception:
        return denied(
            "POST_RESUME_INVENTORY_UNAVAILABLE",
            before=len(before), accepted=accepted,
        )
    if after is None:
        return denied(
            "POST_RESUME_INVENTORY_INVALID",
            before=len(before), accepted=accepted,
        )
    if len(after - before) > 1:
        return denied(
            "UNEXPECTED_CONCURRENT_SESSION_MUTATION",
            before=len(before), after=len(after), accepted=accepted,
        )
    if namespace not in after:
        return denied(
            "RESUME_DID_NOT_CREATE_NAMESPACE",
            before=len(before), after=len(after), accepted=accepted,
        )
    if not accepted:
        return denied(
            "RESUME_RETURN_INDETERMINATE",
            before=len(before), after=len(after), newly=True,
        )
    return denied(
        "NEW_NAME_PRESENT_BUT_PHYSICAL_OWNERSHIP_UNVERIFIED",
        state="ISOLATED_NAMESPACE_CANDIDATE_UNATTESTED",
        before=len(before), after=len(after), accepted=True, newly=True,
    )
