"""Chrome Use no-send reconciliation aid for an already existing session.

The audit uses only three read commands: 'session list', 'get url' and 'tab
list'. It never creates sessions, adopts tabs, brings a tab to the foreground,
navigates, sends, edits local state, or prints private URLs. A matching
tab is NOT an authorization to bind or resume a GPT conversation.

This is intentionally separate from ChromeUseActorDriverV3 because legacy
driver.sessions can be empty even when Chrome Use has a live session.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .session_admission import _canonical_conversation_url


@dataclass(frozen=True)
class BrowserBindingAudit:
    status: str
    reason: str
    discovered_sessions: int = 0
    chatgpt_conversations: int = 0
    matching_master_tabs: int = 0
    selected_url_matches_master: bool = False
    browser_send_authorized: bool = False
    browser_adoption_authorized: bool = False


def _extract_scalar(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("url", "value", "result", "text", "snapshot", "data"):
            if key in value:
                found = _extract_scalar(value[key])
                if found is not None:
                    return found
    if isinstance(value, list) and len(value) == 1:
        return _extract_scalar(value[0])
    return None


def _tab_records(tree: Any) -> list[tuple[str, str | None]]:
    records: list[tuple[str, str | None]] = []
    def walk(value: Any, depth: int) -> None:
        if depth > 12 or len(records) >= 128:
            return
        if isinstance(value, dict):
            url = value.get("url")
            if isinstance(url, str):
                own = value.get("ownership")
                records.append((url, own if isinstance(own, str) else None))
            for item in value.values():
                if isinstance(item, (dict, list)):
                    walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value[:128]:
                walk(item, depth + 1)
    walk(tree, 0)
    return records


def inspect_existing_chrome_use_session(
    executable: str,
    expected_master_url: str,
    *,
    run: Callable[..., Any] = subprocess.run,
    timeout_seconds: float = 10.0,
    unresolved_master_intents: int | None = None,
    rotation_conflict: bool | None = None,
) -> BrowserBindingAudit:
    """Read-only inventory; a successful result still requires human binding review."""
    def blocked(reason: str, *, n=0, chat=0, matches=0, selected=False):
        return BrowserBindingAudit("BLOCKED", reason, n, chat, matches, selected)
    master = _canonical_conversation_url(expected_master_url)
    if not master:
        return blocked("MASTER_CANONICAL_URL_UNVERIFIED")
    if not isinstance(executable, str) or not executable.strip():
        return blocked("CLI_EXECUTABLE_MISSING")
    if type(unresolved_master_intents) is not int or unresolved_master_intents < 0:
        return blocked("INTENT_COUNT_UNVERIFIED")
    if type(rotation_conflict) is not bool:
        return blocked("ROTATION_STATUS_UNVERIFIED")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
        return blocked("CLI_TIMEOUT_INVALID")
    def call(*args: str) -> Any:
        proc = run(
            [executable, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_seconds,
        )
        if proc.returncode != 0:
            raise RuntimeError("CLI_NONZERO")
        if len(proc.stdout) > 256000:
            raise ValueError("CLI_JSON_TOO_LARGE")
        payload = json.loads(proc.stdout)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError("CLI_REPORTED_FAILURE")
        return payload

    try:
        listed = call("--json", "session", "list")
    except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError):
        return blocked("SESSION_INVENTORY_UNAVAILABLE")
    sessions = listed.get("sessions") if isinstance(listed, dict) else None
    if not isinstance(sessions, list):
        return blocked("SESSION_INVENTORY_SHAPE_UNKNOWN")
    if len(sessions) != 1:
        return blocked("UNIQUE_SESSION_REQUIRED", n=len(sessions))
    item = sessions[0]
    name = item if isinstance(item, str) else next(
        (item[k] for k in ("name", "session", "id")
         if isinstance(item, dict) and isinstance(item.get(k), str)), None,
    )
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", name) is None:
        return blocked("SESSION_IDENTIFIER_UNVERIFIED", n=1)
    try:
        focused = _extract_scalar(call("--session", name, "--json", "get", "url"))
        tabs = _tab_records(call("--session", name, "--json", "tab", "list"))
    except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError):
        return blocked("READ_ONLY_SESSION_OBSERVATION_FAILED", n=1)

    chat = [(_canonical_conversation_url(url), ownership) for url, ownership in tabs]
    chat = [(url, ownership) for url, ownership in chat if url is not None]
    match_count = sum(url == master for url, _ownership in chat)
    focused_match = _canonical_conversation_url(focused or "") == master
    if rotation_conflict:
        return blocked("MASTER_ROTATION_CONFLICT_UNRESOLVED", n=1, chat=len(chat),
                       matches=match_count, selected=focused_match)
    if unresolved_master_intents:
        return blocked("LEGACY_MASTER_INTENT_UNRESOLVED", n=1, chat=len(chat),
                       matches=match_count, selected=focused_match)
    if match_count > 1:
        return blocked("MASTER_TAB_DUPLICATED", n=1, chat=len(chat),
                       matches=match_count, selected=focused_match)
    if match_count == 0:
        return blocked("CANONICAL_MASTER_TAB_NOT_FOUND", n=1, chat=len(chat))
    if not focused_match:
        return blocked("MASTER_TAB_NOT_CURRENTLY_BOUND", n=1, chat=len(chat),
                       matches=match_count)
    owner = next(ownership for url, ownership in chat if url == master)
    if owner not in {"created", "adopted"}:
        return blocked("MASTER_TAB_NOT_OWNED_BY_SESSION", n=1, chat=len(chat),
                       matches=match_count, selected=focused_match)
    return BrowserBindingAudit(
        "READONLY_MATCH_REVIEW_REQUIRED",
        "CANONICAL_URL_FOUND_BUT_NO_PHYSICAL_ATTESTATION_OR_BIND_PERMISSION",
        discovered_sessions=1, chatgpt_conversations=len(chat),
        matching_master_tabs=1, selected_url_matches_master=True,
    )
