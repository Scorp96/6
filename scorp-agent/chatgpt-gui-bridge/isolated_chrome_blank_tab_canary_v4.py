"""One-shot, no-send blank-tab creation in a fresh Chrome Use session.

Unlike Chrome Use `session resume` (USER handoff recovery), issuing an
explicit new-tab command in a *fresh, namespaced* session is documented to
create a session-owned tab group on the extension transport. This script
never adopts, selects, activates, navigates an existing tab, or sends text.

A durable SQLite ATTEMPT_RESERVED record is committed BEFORE browser I/O.
Crashes, timeouts, ambiguous results, repeated Issue delivery and restarts
therefore cannot automatically create a second tab for the same namespace.

Even a successful blank-tab observation is only a no-send session candidate,
not proof of Chrome profile separation or authority to contact ChatGPT.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import contextlib
import json
import os
import sqlite3
import stat

from isolated_chrome_namespace_admission_v4 import (
    _CANARY, _sessions, inspect_fresh_namespace_without_tabs,
)


@dataclass(frozen=True)
class BlankTabCanary:
    status: str
    reason: str
    attempt_reserved: bool = False
    command_accepted: bool = False
    session_name_appeared: bool = False
    new_blank_tab_confirmed: bool = False
    browser_send_authorized: bool = False
    chatgpt_navigation_authorized: bool = False
    host_terminal_event_verified: bool = False
    physical_profile_isolation_verified: bool = False


def _safe_ledger_path(ledger: Path, allowed_experiments_root: Path) -> bool:
    """Reject absolute/external/root/symlink/hardlinked SQLite targets."""
    if not isinstance(ledger, Path) or not isinstance(allowed_experiments_root, Path):
        return False
    try:
        root = allowed_experiments_root.resolve(strict=True)
        parent = ledger.parent.resolve(strict=True)
        if (
            parent == root or root not in parent.parents
            # Dedicated canary scratch folder ONLY. Never let a syntactically
            # valid r2-* directory include a frozen production observer,
            # earlier isolated source worktree or other preexisting project.
            or not parent.name.startswith("r2-one-shot-blank-tab-")
            or ledger.name != "blank-tab-attempts.sqlite3"
            or ledger.is_symlink()
        ):
            return False
        # Avoid writing through junction/symlink ancestors on Windows. Python
        # resolve already canonicalizes them; also disallow a hardlinked file.
        if ledger.exists():
            s = ledger.stat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _reserve_before_browser_io(ledger: Path, namespace: str) -> str:
    try:
        with contextlib.closing(sqlite3.connect(
            str(ledger), isolation_level=None, timeout=3,
        )) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS one_shot_blank_tabs(
                        namespace TEXT PRIMARY KEY,
                        state TEXT NOT NULL CHECK(
                            state IN ('ATTEMPT_RESERVED','OBSERVED_UNATTESTED')
                        ),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                if db.execute(
                    "SELECT 1 FROM one_shot_blank_tabs WHERE namespace=?",
                    (namespace,),
                ).fetchone():
                    db.rollback()
                    return "ALREADY_RESERVED_NO_RETRY"
                db.execute(
                    "INSERT INTO one_shot_blank_tabs(namespace,state) "
                    "VALUES(?, 'ATTEMPT_RESERVED')",
                    (namespace,),
                )
                db.commit()
                return "ATTEMPT_RESERVED"
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return "DURABLE_RESERVATION_FAILED"


def _mark_observed(ledger: Path, namespace: str) -> None:
    try:
        with contextlib.closing(sqlite3.connect(str(ledger), timeout=2)) as db:
            db.execute(
                "UPDATE one_shot_blank_tabs SET state='OBSERVED_UNATTESTED' "
                "WHERE namespace=? AND state='ATTEMPT_RESERVED'",
                (namespace,),
            )
            db.commit()
    except (sqlite3.Error, OSError):
        # Never retry a possibly created tab because an audit write failed.
        pass


async def attempt_one_blank_tab_canary(
    cli,
    *,
    namespace: str,
    ledger: Path,
    allowed_experiments_root: Path,
    allow_new_blank_tab: bool = False,
    timeout_seconds: int = 15,
) -> BlankTabCanary:
    """One permitted blank tab; no login, submit, navigation or retries."""
    def outcome(state, why, reserved=False, accepted=False, appeared=False, tab=False):
        return BlankTabCanary(state, why, reserved, accepted, appeared, tab)

    if allow_new_blank_tab is not True:
        return outcome("BLOCKED", "EXPLICIT_BLANK_TAB_CANARY_PERMISSION_REQUIRED")
    if not isinstance(namespace, str) or _CANARY.fullmatch(namespace) is None:
        return outcome("BLOCKED", "ISOLATED_NAMESPACE_INVALID")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        return outcome("BLOCKED", "TIMEOUT_INVALID")
    if not _safe_ledger_path(ledger, allowed_experiments_root):
        return outcome("BLOCKED", "ISOLATED_LEDGER_PATH_UNSAFE")

    current = await inspect_fresh_namespace_without_tabs(
        cli, namespace=namespace, timeout_seconds=timeout_seconds,
    )
    if current.status != "NAMESPACE_NAME_AVAILABLE_UNATTESTED":
        return outcome("BLOCKED", "PRE_CREATION:" + current.reason)

    # Durable commit BEFORE any browser-side action.
    reservation = _reserve_before_browser_io(ledger, namespace)
    if reservation != "ATTEMPT_RESERVED":
        return outcome("BLOCKED", reservation)

    send = getattr(cli, "run_json", None)
    list_sessions = getattr(cli, "list_sessions_readonly", None)
    list_tabs = getattr(cli, "list_tabs_readonly", None)
    if not callable(send) or not callable(list_sessions) or not callable(list_tabs):
        return outcome("BLOCKED", "BROWSER_ADAPTER_UNAVAILABLE", True)
    try:
        response = await send(
            namespace, "tab", "new", "about:blank",
            timeout_seconds=timeout_seconds,
        )
    except BaseException:
        # Includes cancellation: the child may have created the blank tab.
        return outcome("BLOCKED", "BLANK_TAB_CREATE_OUTCOME_UNKNOWN", True)
    if not isinstance(response, dict):
        return outcome("BLOCKED", "BLANK_TAB_CREATE_RESPONSE_INVALID", True)

    accepted = response.get("success") is True or response.get("ok") is True
    body = response.get("data")
    tab_id = body.get("tabId") if isinstance(body, dict) else None
    if not accepted or not isinstance(tab_id, str) or not tab_id.strip():
        return outcome("BLOCKED", "BLANK_TAB_CREATE_NOT_CONFIRMED", True, accepted)

    try:
        sessions = _sessions(await list_sessions(timeout_seconds=timeout_seconds))
    except Exception:
        return outcome("BLOCKED", "POST_CREATION_SESSION_UNAVAILABLE", True, accepted)
    if sessions is None or namespace not in sessions:
        return outcome("BLOCKED", "NEW_SESSION_NOT_VISIBLE", True, accepted)
    try:
        observed = await list_tabs(namespace, timeout_seconds=timeout_seconds)
    except Exception:
        return outcome("BLOCKED", "POST_CREATION_TABS_UNAVAILABLE", True, accepted, True)
    if not isinstance(observed, dict) or observed.get("success") is not True:
        return outcome("BLOCKED", "POST_CREATION_TABS_INVALID", True, accepted, True)
    data = observed.get("data")
    tabs = data.get("tabs") if isinstance(data, dict) and data.get("full") is True else None
    if not isinstance(tabs, list):
        return outcome("BLOCKED", "FULL_TAB_METADATA_MISSING", True, accepted, True)
    matched = [t for t in tabs if isinstance(t, dict) and t.get("tabId") == tab_id]
    if len(matched) != 1:
        return outcome("BLOCKED", "CREATED_TAB_NOT_UNIQUELY_VISIBLE", True, accepted, True)
    tab = matched[0]
    if (tab.get("url") != "about:blank" or tab.get("type") != "page"
            or tab.get("relayAttached") is not True):
        return outcome("BLOCKED", "BLANK_TAB_PHYSICAL_PROPERTIES_UNVERIFIED",
                       True, accepted, True)
    _mark_observed(ledger, namespace)
    return outcome(
        "BLANK_TAB_CANDIDATE_UNATTESTED",
        "FRESH_NAMESPACE_AND_BLANK_TAB_OBSERVED_NO_SEND_AUTHORITY",
        True, True, True, True,
    )
