"""Read-only physical Chrome Use *presence*, not trusted GPT completion.

Evidence shape verified against the user's Windows Chrome Use 2026-10-09:
session list: {ok: bool, sessions: [{name,owner,pid}]};
tab list --full: {success:bool,data:{full:bool,tabs:[
{active,ownership,relayAttached,tabId,targetId,title,type,url}]}}.
These are diagnostics. Neither a URL nor relayAttached is proof of login,
prompt authorship, model identity, or a host-issued TURN_FINAL_CONFIRMED event.

NO navigation, activation, tab selection, authentication or message sends.
Never expose real session IDs, URLs, tab titles or account data in outputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gui_transport import validate_conversation_url


@dataclass(frozen=True)
class ChromePhysicalPresenceResult:
    status: str
    reason: str
    session_count: int | None = None
    matching_tab_count: int = 0
    browser_send_authorized: bool = False
    host_terminal_event_verified: bool = False
    auth_verified: bool = False
    physical_binding_verified: bool = False


async def inspect_pinned_chrome_tabs_readonly(
    cli: Any,
    *,
    expected_session: str,
    expected_conversation_url: str,
    timeout_seconds: float = 10,
) -> ChromePhysicalPresenceResult:
    """Observe exact pre-owned session; NEVER adopt from live inventory."""
    def denied(reason: str, *, status: str = "BLOCKED", count: int | None = None,
               matching: int = 0) -> ChromePhysicalPresenceResult:
        return ChromePhysicalPresenceResult(status, reason, count, matching)

    if (
        not isinstance(expected_session, str)
        or not expected_session.strip()
        or len(expected_session) > 128
        or expected_session != expected_session.strip()
        or type(timeout_seconds) not in (int, float)
        or not 0 < timeout_seconds <= 30
    ):
        return denied("PINNED_SESSION_OR_TIMEOUT_INVALID")
    try:
        url = validate_conversation_url(expected_conversation_url)
    except (ValueError, TypeError):
        return denied("PINNED_CONVERSATION_INVALID")

    list_sessions = getattr(cli, "list_sessions_readonly", None)
    list_tabs = getattr(cli, "list_tabs_readonly", None)
    if not callable(list_sessions) or not callable(list_tabs):
        return denied("READONLY_ADAPTER_CAPABILITY_MISSING")

    try:
        inventory = await list_sessions(timeout_seconds=timeout_seconds)
    except Exception:
        return denied("SESSION_INVENTORY_UNAVAILABLE")
    if not isinstance(inventory, dict) or inventory.get("ok") is not True:
        return denied("SESSION_INVENTORY_INVALID")
    sessions = inventory.get("sessions")
    if not isinstance(sessions, list) or len(sessions) > 256:
        return denied("SESSION_LIST_INVALID")
    matched = [
        row for row in sessions
        if isinstance(row, dict) and row.get("name") == expected_session
    ]
    if len(matched) != 1:
        return denied(
            "PINNED_SESSION_MISSING" if not matched else "DUPLICATE_SESSION_ID",
            count=len(sessions),
        )
    # The backend chooses and attests the session name. No session is created,
    # adopted, or inferred from the potentially unrelated list of live tabs.
    try:
        inventory = await list_tabs(expected_session, timeout_seconds=timeout_seconds)
    except Exception:
        return denied("TAB_INVENTORY_UNAVAILABLE", count=len(sessions))
    if not isinstance(inventory, dict) or inventory.get("success") is not True:
        return denied("TAB_INVENTORY_INVALID", count=len(sessions))
    data = inventory.get("data")
    if not isinstance(data, dict) or data.get("full") is not True:
        return denied("FULL_TAB_METADATA_REQUIRED", count=len(sessions))
    tabs = data.get("tabs")
    if not isinstance(tabs, list) or len(tabs) > 256:
        return denied("TAB_LIST_INVALID", count=len(sessions))

    hits = []
    other_chatgpt = 0
    for tab in tabs:
        if not isinstance(tab, dict):
            return denied("TAB_ENTRY_INVALID", count=len(sessions))
        raw = tab.get("url")
        if not isinstance(raw, str):
            return denied("TAB_URL_MISSING", count=len(sessions))
        if raw == url:
            hits.append(tab)
        elif raw.startswith("https://chatgpt.com/c/"):
            # Another ChatGPT conversation in the pinned session is
            # ambiguous. Never silently promote/focus a seemingly good tab.
            other_chatgpt += 1

    if len(hits) == 0:
        return denied(
            "PINNED_CHATGPT_TAB_ABSENT", count=len(sessions), matching=0,
        )
    if len(hits) != 1 or other_chatgpt:
        return denied(
            "MULTIPLE_CHATGPT_TABS_UNVERIFIED",
            count=len(sessions), matching=len(hits),
        )
    item = hits[0]
    if item.get("active") is not True or item.get("relayAttached") is not True:
        return denied(
            "PINNED_TAB_NOT_ACTIVE_OR_ATTACHED",
            count=len(sessions), matching=1,
        )
    if item.get("type") != "page":
        return denied("PINNED_TAB_TYPE_INVALID", count=len(sessions), matching=1)

    return denied(
        "EXACT_TAB_URL_MATCH_ONLY_NO_HOST_ATTESTATION",
        status="PRESENCE_CANDIDATE_UNATTESTED",
        count=len(sessions), matching=1,
    )
