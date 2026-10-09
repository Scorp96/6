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


async def inspect_fresh_namespace_without_tabs(
    cli: Any,
    *,
    namespace: str,
    timeout_seconds: int = 10,
) -> IsolatedNamespaceAdmission:
    """Only inspect pre-existing session names; no session handoff/resume.

    Chrome Use documentation defines `session resume` as taking control BACK
    from a user handoff. It does NOT create a fresh session. Our previous
    experimentally checked resume path was invalid and is now retired.
    """
    def denied(reason: str, state: str = "BLOCKED", before: int | None = None):
        return IsolatedNamespaceAdmission(state, reason, before)
    if (
        not isinstance(namespace, str)
        or _CANARY.fullmatch(namespace) is None
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 30
    ):
        return denied("ISOLATED_NAMESPACE_OR_TIMEOUT_INVALID")
    listing = getattr(cli, "list_sessions_readonly", None)
    if not callable(listing):
        return denied("READONLY_SESSION_CAPABILITY_UNAVAILABLE")
    try:
        names = _sessions(await listing(timeout_seconds=timeout_seconds))
    except Exception:
        return denied("PRE_CREATION_INVENTORY_UNAVAILABLE")
    if names is None:
        return denied("PRE_CREATION_INVENTORY_INVALID")
    if namespace in names:
        return denied("ISOLATED_NAMESPACE_ALREADY_EXISTS", before=len(names))
    return denied(
        "NEW_NAMESPACE_NAME_AVAILABLE_NOT_PHYSICAL_OWNERSHIP_PROOF",
        state="NAMESPACE_NAME_AVAILABLE_UNATTESTED",
        before=len(names),
    )


async def attempt_isolated_namespace_resume_without_tabs(
    cli: Any,
    *,
    namespace: str,
    timeout_seconds: int = 10,
) -> IsolatedNamespaceAdmission:
    """Retired unsafe experiment: session resume is for USER HANDOFF only.

    Never run it as a namespace constructor. No Chrome Use calls, even if
    clients still import the historical function.
    """
    return IsolatedNamespaceAdmission(
        "BLOCKED", "SESSION_RESUME_HANDOFF_ONLY_NOT_SESSION_CREATION",
    )
