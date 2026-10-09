"""Passive recovery witness for a PREVIOUSLY established Chrome Use session.

Chrome Use session list reports currently running per-session daemon workers;
it is not an authoritative inventory of physical Chrome tab ownership.
An absent worker must NOT cause a new tab or session resume command.

A previous one-shot SQLite OBSERVED_UNATTESTED receipt is necessary before
even probing the dormant namespace with read-only tab list --full. The tab
list may lazily re-establish its worker, but this module only accepts
already-created owned blank tabs and never selects/navigates/sends.

No return status grants actor ownership, authentication, GPT completion,
browser-send, or local-execution authorization.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

from isolated_chrome_blank_tab_canary_v4 import _safe_ledger_path
from isolated_chrome_namespace_admission_v4 import _CANARY, _sessions


@dataclass(frozen=True)
class DormantOwnedSessionWitness:
    status: str
    reason: str
    previously_active: bool = False
    active_after_observation: bool = False
    owned_blank_tabs_observed: int = 0
    read_only_tab_probe_attempted: bool = False
    tab_navigation_authorized: bool = False
    authenticated: bool = False
    host_terminal_event_verified: bool = False
    browser_send_authorized: bool = False
    local_execution_authorized: bool = False


def _prior_blank_receipt(ledger: Path, namespace: str) -> bool:
    if not ledger.is_file():
        return False
    try:
        uri = ledger.resolve(strict=True).as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                return False
            row = db.execute(
                "SELECT state FROM one_shot_blank_tabs WHERE namespace=?",
                (namespace,),
            ).fetchone()
        return row == ("OBSERVED_UNATTESTED",)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False


def _owned_blank_count(payload: Any) -> int | None:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("full") is not True:
        return None
    tabs = data.get("tabs")
    if not isinstance(tabs, list) or len(tabs) > 16:
        return None
    seen: set[tuple[str, str]] = set()
    count = 0
    for tab in tabs:
        if not isinstance(tab, dict):
            return None
        tab_id, target = tab.get("tabId"), tab.get("targetId")
        if (not isinstance(tab_id, str) or not tab_id
                or not isinstance(target, str) or not target):
            return None
        key = (tab_id, target)
        if key in seen:
            return None
        seen.add(key)
        if (tab.get("url") != "about:blank"
                or tab.get("ownership") != "created"
                or tab.get("type") != "page"
                or tab.get("relayAttached") is not True):
            return None
        count += 1
    return count


async def inspect_previously_owned_blank_session_readonly(
    cli: Any,
    *,
    namespace: str,
    original_canary_ledger: Path,
    allowed_experiments_root: Path,
    expected_created_blank_count: int = 2,
    timeout_seconds: int = 12,
) -> DormantOwnedSessionWitness:
    def denied(reason: str, *, state: str = "BLOCKED",
               before: bool = False, after: bool = False,
               count: int = 0, attempted: bool = False) -> DormantOwnedSessionWitness:
        return DormantOwnedSessionWitness(state, reason, before, after, count, attempted)

    if (
        not isinstance(namespace, str) or _CANARY.fullmatch(namespace) is None
        or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 25
        or type(expected_created_blank_count) is not int
        or not 1 <= expected_created_blank_count <= 6
    ):
        return denied("RECOVERY_SCOPE_INVALID")
    if (
        not _safe_ledger_path(original_canary_ledger, allowed_experiments_root)
        or not _prior_blank_receipt(original_canary_ledger, namespace)
    ):
        return denied("DURABLE_PREVIOUS_SESSION_PROOF_MISSING")
    listing = getattr(cli, "list_sessions_readonly", None)
    tabs = getattr(cli, "list_tabs_readonly", None)
    if not callable(listing) or not callable(tabs):
        return denied("READ_ONLY_TAB_CAPABILITY_REQUIRED")
    try:
        before_names = _sessions(await listing(timeout_seconds=timeout_seconds))
    except Exception:
        return denied("SESSION_INVENTORY_BEFORE_UNAVAILABLE")
    if before_names is None:
        return denied("SESSION_INVENTORY_BEFORE_INVALID")
    before_active = namespace in before_names

    # Exactly one read-only targeted tab-list command, never session resume,
    # adopt, create, open, select, fill, keyboard, click or URL navigation.
    try:
        owned = _owned_blank_count(
            await tabs(namespace, timeout_seconds=timeout_seconds),
        )
    except Exception:
        return denied(
            "OWNED_TAB_OBSERVATION_FAILED", before=before_active,
            attempted=True,
        )
    if owned != expected_created_blank_count:
        return denied(
            "PREVIOUSLY_CREATED_TAB_GROUP_UNVERIFIED",
            before=before_active, count=owned if owned is not None else 0,
            attempted=True,
        )
    try:
        after_names = _sessions(await listing(timeout_seconds=timeout_seconds))
    except Exception:
        return denied(
            "SESSION_INVENTORY_AFTER_UNAVAILABLE",
            before=before_active, count=owned, attempted=True,
        )
    if after_names is None:
        return denied(
            "SESSION_INVENTORY_AFTER_INVALID",
            before=before_active, count=owned, attempted=True,
        )
    if namespace not in after_names:
        return denied(
            "OWNED_TABS_OBSERVED_BUT_DAEMON_NOT_LISTED",
            before=before_active, count=owned, attempted=True,
        )
    return denied(
        "PREVIOUSLY_CREATED_BLANK_TABS_ONLY_NOT_MASTER_OR_SEND_PROOF",
        state="OWNED_BLANK_SESSION_OBSERVED_UNATTESTED",
        before=before_active, after=True, count=owned, attempted=True,
    )
