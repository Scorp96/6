"""Offline, fail-closed comparison of Chrome Use tab inventories.

Evidence scope: SAME logical session, two authenticated-invocation observations.
A missing tab in `tab list --full` is an inventory fact, NOT proof that
the Chrome tab was physically closed. Renderer, daemon and relay lifetime
can differ. Never adopt a new identity or grant browser-send permission.

This module performs no browser or local system operations and never returns
actual tab identifiers, URLs, titles, page content, or personal metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_HOME = ("https://chatgpt.com/", "https://chatgpt.com")


@dataclass(frozen=True)
class TabTransitionEvidence:
    status: str
    reason: str
    old_count: int = 0
    after_count: int = 0
    old_exact_retained: int = 0
    old_no_longer_listed: int = 0
    previously_unknown_targets: int = 0
    new_created_home_count: int = 0
    old_tab_alias_changed: bool = False
    old_target_changed: bool = False
    physical_tab_closed_proven: bool = False
    authentication_attested: bool = False
    host_completion_attested: bool = False
    browser_send_authorized: bool = False


def _inventory(payload: Any) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("full") is not True:
        return None
    rows = data.get("tabs")
    if not isinstance(rows, list) or len(rows) > 32:
        return None
    tab_ids: set[str] = set()
    targets: set[str] = set()
    result = []
    for t in rows:
        if not isinstance(t, dict):
            return None
        tab_id, target = t.get("tabId"), t.get("targetId")
        if (
            not isinstance(tab_id, str) or not tab_id or len(tab_id) > 256
            or not isinstance(target, str) or not target or len(target) > 256
            or tab_id in tab_ids or target in targets
            or t.get("ownership") not in ("created", "adopted", "foreign")
            or not isinstance(t.get("url"), str)
            or t.get("type") != "page"
            or not isinstance(t.get("relayAttached"), bool)
        ):
            return None
        tab_ids.add(tab_id)
        targets.add(target)
        result.append(t)
    return result


def compare_created_blank_to_homepage(
    before: Any, after: Any, *, expected_new_tab_id: str | None = None,
) -> TabTransitionEvidence:
    """No side effect. Counts only; never return source IDs or strings."""
    def blocked(reason, *, prior=0, post=0, kept=0, missing=0,
                novel=0, home=0, alias=False, target_changed=False,
                state="BLOCKED"):
        return TabTransitionEvidence(
            state, reason, prior, post, kept, missing, novel, home,
            alias, target_changed,
        )
    if (
        expected_new_tab_id is not None
        and (not isinstance(expected_new_tab_id, str)
             or not expected_new_tab_id or len(expected_new_tab_id) > 256)
    ):
        return blocked("EXPECTED_NEW_TAB_ID_INVALID")
    prior = _inventory(before)
    post = _inventory(after)
    if prior is None or post is None or not 1 <= len(prior) <= 6:
        return blocked("FULL_UNIQUE_TAB_INVENTORY_REQUIRED")
    if any(
        t.get("ownership") != "created" or t.get("url") != "about:blank"
        or t.get("relayAttached") is not True
        for t in prior
    ):
        return blocked("PREVIOUS_OWNED_BLANK_BOUNDARY_UNVERIFIED")

    prior_pairs = {(t["targetId"], t["tabId"]) for t in prior}
    post_pairs = {(t["targetId"], t["tabId"]) for t in post}
    prior_target = {t["targetId"] for t in prior}
    post_target = {t["targetId"] for t in post}
    prior_alias = {t["tabId"] for t in prior}
    post_alias = {t["tabId"] for t in post}
    exact = len(prior_pairs & post_pairs)
    missing = len(prior_pairs - post_pairs)
    novel = len(post_target - prior_target)
    alias_changed = bool(prior_target & post_target) and exact != len(prior_target & post_target)
    target_changed = bool(prior_alias & post_alias) and exact != len(prior_alias & post_alias)
    home_tabs = [
        t for t in post
        if t.get("ownership") == "created"
        and t.get("url") in _HOME
        and t.get("relayAttached") is True
    ]
    basic = {
        "prior": len(prior), "post": len(post), "kept": exact,
        "missing": missing, "novel": novel, "home": len(home_tabs),
        "alias": alias_changed, "target_changed": target_changed,
    }
    def result(reason: str, state="BLOCKED"):
        return blocked(
            reason, state=state, **basic,
        )

    # The absence of an existing target from a session's list does NOT
    # establish it was closed, disposed, or removed from physical Chrome.
    if target_changed:
        return result("SESSION_TAB_ID_REUSED_FOR_OTHER_TARGET")
    if alias_changed:
        return result("STABLE_TARGET_SESSION_LOCAL_ALIAS_CHANGED")
    if missing:
        return result("PREVIOUS_TABS_NO_LONGER_LISTED")
    if not home_tabs:
        return result("EXPECTED_HOME_TAB_NOT_OBSERVED")
    if len(home_tabs) != 1 or novel != 1 or len(post) != len(prior) + 1:
        return result("EXACTLY_ONE_NEW_HOME_TAB_UNVERIFIED")
    newly_created = home_tabs[0]
    if (newly_created.get("targetId") in prior_target
            or (
                expected_new_tab_id is not None
                and newly_created.get("tabId") != expected_new_tab_id
            )):
        return result("EXPECTED_NEW_TAB_IDENTITY_MISMATCH")
    if any(
        (t["targetId"], t["tabId"]) in prior_pairs
        and (t.get("url") != "about:blank"
             or t.get("ownership") != "created"
             or t.get("relayAttached") is not True)
        for t in post
    ):
        return result("PRIOR_TAB_URL_OR_OWNERSHIP_CHANGED")
    return result(
        "ONE_NEW_CREATED_HOME_TARGET_OLD_TABS_PRESERVED_NO_SEND_AUTHORITY",
        state="HOME_INVENTORY_CANDIDATE_UNATTESTED",
    )
