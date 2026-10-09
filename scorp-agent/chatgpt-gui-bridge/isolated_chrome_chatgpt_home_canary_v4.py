"""One-shot no-send ChatGPT homepage tab experiment in an ALREADY owned R2 session.

This does NOT generate, submit, type, fill, click, read private transcripts
or verify GPT completion. This uses a new tab, not an existing Master tab.
All external page loads happen only after an on-disk one-shot reservation has
been durably committed in the dedicated isolated experiment directory.

No code in this module authorizes browser sends, live Worker execution,
reusing another actor's tab, authentication claims or GPT final events.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import contextlib
import hashlib
import json
import os
import sqlite3
import stat
from urllib.parse import urlsplit

from isolated_chrome_namespace_admission_v4 import _CANARY, _sessions

_URL = "https://chatgpt.com/"
_DB_NAME = "chatgpt-home-no-send-once.sqlite3"


@dataclass(frozen=True)
class HomepageCanaryDecision:
    status: str
    reason: str
    command_reserved: bool = False
    command_attempted: bool = False
    new_tab_confirmed: bool = False
    old_tabs_preserved: bool = False
    authenticated: bool = False
    host_terminal_verified: bool = False
    browser_send_authorized: bool = False
    prompt_submission_attempted: bool = False


def _safe_path(path: Path, root: Path) -> bool:
    if not isinstance(path, Path) or not isinstance(root, Path):
        return False
    try:
        root_real = root.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
        if (parent == root_real or root_real not in parent.parents
                or not parent.name.startswith("r2-one-shot-blank-tab-")
                or path.name != _DB_NAME or path.is_symlink()):
            return False
        if path.exists():
            st = path.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _reserve(path: Path, namespace: str) -> str:
    try:
        with contextlib.closing(sqlite3.connect(
            str(path), isolation_level=None, timeout=3,
        )) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS homepage_once(
                        session_name TEXT PRIMARY KEY,
                        status TEXT NOT NULL CHECK(status IN (
                            'ATTEMPT_RESERVED', 'ROOT_OBSERVED_UNATTESTED')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                if db.execute(
                    "SELECT 1 FROM homepage_once WHERE session_name=?",
                    (namespace,),
                ).fetchone():
                    db.rollback()
                    return "ALREADY_RESERVED_NO_RETRY"
                db.execute(
                    "INSERT INTO homepage_once(session_name,status) "
                    "VALUES(?, 'ATTEMPT_RESERVED')", (namespace,),
                )
                db.commit()
                return "ATTEMPT_RESERVED"
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        return "RESERVATION_UNAVAILABLE"


def _commit_observed(path: Path, namespace: str) -> None:
    try:
        with contextlib.closing(sqlite3.connect(str(path), timeout=2)) as db:
            db.execute(
                "UPDATE homepage_once SET status='ROOT_OBSERVED_UNATTESTED' "
                "WHERE session_name=? AND status='ATTEMPT_RESERVED'",
                (namespace,),
            )
            db.commit()
    except (sqlite3.Error, OSError):
        pass


def _safe_home_url(value: object) -> bool:
    # Even if a new tab navigated to a conversation, no turns can be sent.
    # Here, only the exact intended homepage URL counts as a successful load.
    if not isinstance(value, str):
        return False
    return value == _URL or value == "https://chatgpt.com"


def _tab_fingerprint(t: dict) -> tuple[str, str] | None:
    target = t.get("targetId")
    tab_id = t.get("tabId")
    if not isinstance(target, str) or not target or not isinstance(tab_id, str) or not tab_id:
        return None
    return (target, tab_id)


def _owned_blank_tab_snapshot(payload: Any) -> set[tuple[str, str]] | None:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("full") is not True:
        return None
    tabs = data.get("tabs")
    if not isinstance(tabs, list) or not 1 <= len(tabs) <= 6:
        return None
    values: set[tuple[str, str]] = set()
    for tab in tabs:
        if not isinstance(tab, dict):
            return None
        key = _tab_fingerprint(tab)
        if (key is None or key in values
                or tab.get("url") != "about:blank"
                or tab.get("type") != "page"
                or tab.get("ownership") != "created"
                or tab.get("relayAttached") is not True):
            return None
        values.add(key)
    return values


async def inspect_and_create_one_homepage_tab_no_send(
    cli: Any,
    *,
    namespace: str,
    ledger: Path,
    allowed_experiments_root: Path,
    authorized_homepage_canary: bool = False,
    timeout_seconds: int = 12,
) -> HomepageCanaryDecision:
    """One-shot new homepage tab. No selection, keyboard or message send."""
    def result(state: str, reason: str, reserved: bool = False,
               attempted: bool = False, confirmed: bool = False,
               preserved: bool = False) -> HomepageCanaryDecision:
        return HomepageCanaryDecision(state, reason, reserved, attempted, confirmed, preserved)

    if authorized_homepage_canary is not True:
        return result("BLOCKED", "EXPLICIT_NO_SEND_HOME_CANARY_AUTHORIZATION_REQUIRED")
    if not isinstance(namespace, str) or _CANARY.fullmatch(namespace) is None:
        return result("BLOCKED", "SESSION_NAMESPACE_INVALID")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 25:
        return result("BLOCKED", "PROBE_TIMEOUT_INVALID")
    if not _safe_path(ledger, allowed_experiments_root):
        return result("BLOCKED", "ISOLATED_LEDGER_SCOPE_UNSAFE")
    session_list = getattr(cli, "list_sessions_readonly", None)
    tab_list = getattr(cli, "list_tabs_readonly", None)
    command = getattr(cli, "run_json", None)
    if not all(callable(fn) for fn in (session_list, tab_list, command)):
        return result("BLOCKED", "READ_ONLY_INVENTORY_OR_BROWSER_ADAPTER_MISSING")
    try:
        names = _sessions(await session_list(timeout_seconds=timeout_seconds))
    except Exception:
        return result("BLOCKED", "PRE_SESSION_INVENTORY_UNAVAILABLE")
    if names is None or namespace not in names or len(names) > 6:
        return result("BLOCKED", "EXACT_PREEXISTING_SESSION_REQUIRED")
    try:
        previous = _owned_blank_tab_snapshot(
            await tab_list(namespace, timeout_seconds=timeout_seconds),
        )
    except Exception:
        return result("BLOCKED", "PRE_OWNED_TAB_INVENTORY_UNAVAILABLE")
    if previous is None:
        return result("BLOCKED", "OWNED_BLANK_TAB_BOUNDARY_UNVERIFIED")

    # This commit is the irreversible retry fence. Below it, all error paths
    # are UNKNOWN and no command is reissued, including external timeouts.
    reservation = _reserve(ledger, namespace)
    if reservation != "ATTEMPT_RESERVED":
        return result("BLOCKED", reservation)
    try:
        created = await command(
            namespace, "tab", "new", _URL, timeout_seconds=timeout_seconds,
        )
    except BaseException:
        return result("BLOCKED", "HOME_TAB_CREATION_EFFECT_AMBIGUOUS", True, True)
    if not isinstance(created, dict) or created.get("success") is not True:
        return result("BLOCKED", "HOME_TAB_RESPONSE_NOT_VERIFIED", True, True)
    body = created.get("data")
    requested_id = body.get("tabId") if isinstance(body, dict) else None
    if not isinstance(requested_id, str) or not requested_id:
        return result("BLOCKED", "HOME_TAB_CREATE_ID_MISSING", True, True)
    try:
        after = await tab_list(namespace, timeout_seconds=timeout_seconds)
    except Exception:
        return result("BLOCKED", "HOME_TAB_OBSERVATION_UNAVAILABLE", True, True)
    if not isinstance(after, dict) or after.get("success") is not True:
        return result("BLOCKED", "HOME_TAB_INVENTORY_INVALID", True, True)
    data = after.get("data")
    tabs = data.get("tabs") if isinstance(data, dict) and data.get("full") is True else None
    if not isinstance(tabs, list) or len(tabs) > 16:
        return result("BLOCKED", "HOME_TAB_LIST_INVALID", True, True)

    remaining: set[tuple[str, str]] = set()
    found: list[dict] = []
    for tab in tabs:
        if not isinstance(tab, dict):
            return result("BLOCKED", "TAB_ENTRY_INVALID", True, True)
        key = _tab_fingerprint(tab)
        if key is None or key in remaining:
            return result("BLOCKED", "TARGET_ID_CONFLICT", True, True)
        remaining.add(key)
        if tab.get("tabId") == requested_id:
            found.append(tab)
    if not previous.issubset(remaining):
        return result("BLOCKED", "EXISTING_OWNED_TAB_CHANGED", True, True)
    if len(found) != 1 or len(remaining - previous) != 1:
        return result("BLOCKED", "EXACTLY_ONE_NEW_HOME_TAB_NOT_PROVEN", True, True)
    new_tab = found[0]
    if (new_tab.get("ownership") != "created"
            or new_tab.get("relayAttached") is not True
            or new_tab.get("type") != "page"):
        return result("BLOCKED", "HOME_TAB_OWNERSHIP_UNVERIFIED", True, True)
    if not _safe_home_url(new_tab.get("url")):
        return result("BLOCKED", "HOME_URL_NOT_EXACT_OR_REDIRECTED", True, True)
    _commit_observed(ledger, namespace)
    return result(
        "HOME_TAB_PRESENT_UNATTESTED",
        "ONE_NEW_CREATED_HOME_TAB_OBSERVED_NO_SEND_AUTHORITY",
        True, True, True, True,
    )
