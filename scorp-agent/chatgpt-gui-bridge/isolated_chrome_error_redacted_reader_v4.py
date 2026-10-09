"""Privacy-preserving read-only bridge for the legacy Chrome Use v3 CLI.

The inherited ChromeUseCliV3 can include native stderr/stdout in exceptions.
This class encapsulates it with a sanitation boundary for NEW isolated
read-only probes; it does not alter the protected running GUI bridge.

The wrapped native transport drops error streams BEFORE the legacy parser
sees them. Invalid, oversized or unsuccessful output becomes a fixed local
failure reason. Public methods emit only counts/boolean categories, never
browser target ids, URLs, account information or page text.

No methods for submit, fill, click, adopt, select, tab new or navigation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any

from chrome_use_cli_v3 import ChromeUseCliV3, _default_runner
from isolated_chrome_namespace_admission_v4 import _CANARY, _sessions


_MAX_STDOUT = 262_144
_HOME = ("https://chatgpt.com", "https://chatgpt.com/")


@dataclass(frozen=True)
class SafeReadOnlyChromeSummary:
    status: str
    reason: str
    active_sessions: int | None = None
    owned_tabs: int | None = None
    chatgpt_home_tabs: int | None = None
    blank_tabs: int | None = None
    browser_send_authorized: bool = False
    terminal_event_verified: bool = False
    source_error_exposed: bool = False


class SafeReadOnlyChromeUseV4:
    def __init__(self, *, executable: str, transport_runner=None):
        runner = transport_runner or _default_runner

        async def redacted(argv, timeout_seconds):
            try:
                rc, stdout, _stderr = await runner(argv, timeout_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The original OS/CLI exception can contain private data.
                raise RuntimeError("ISOLATED_READONLY_TRANSPORT_UNAVAILABLE") from None
            if type(rc) is not int or rc != 0:
                return 69, "", ""
            if not isinstance(stdout, str) or len(stdout.encode("utf-8")) > _MAX_STDOUT:
                return 70, "", ""
            try:
                envelope = json.loads(stdout)
            except (TypeError, ValueError):
                return 71, "", ""
            if not isinstance(envelope, dict):
                return 72, "", ""
            # Verify only parseability, not honesty of the source.
            return 0, stdout, ""

        # Passing a runner disables stdin support for this adapter and avoids
        # an oversized prompt-fill route even if upstream APIs are extended.
        self._cli = ChromeUseCliV3(
            executable=executable, runner=redacted,
        )

    async def inspect_session_counts(
        self, *, timeout_seconds: int = 10,
    ) -> SafeReadOnlyChromeSummary:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 20:
            return SafeReadOnlyChromeSummary("BLOCKED", "READONLY_TIMEOUT_INVALID")
        try:
            values = _sessions(await self._cli.list_sessions_readonly(
                timeout_seconds=timeout_seconds,
            ))
        except Exception:
            return SafeReadOnlyChromeSummary("BLOCKED", "SESSION_LIST_UNAVAILABLE")
        if values is None or len(values) > 64:
            return SafeReadOnlyChromeSummary("BLOCKED", "SESSION_LIST_INVALID")
        return SafeReadOnlyChromeSummary(
            "READONLY_COUNTS_UNATTESTED",
            "ACTIVE_SESSION_COUNT_NOT_A_FINAL_EVENT",
            active_sessions=len(values),
        )

    async def inspect_owned_tabs(
        self, *, isolated_namespace: str, timeout_seconds: int = 10,
    ) -> SafeReadOnlyChromeSummary:
        if (
            not isinstance(isolated_namespace, str)
            or _CANARY.fullmatch(isolated_namespace) is None
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 20
        ):
            return SafeReadOnlyChromeSummary("BLOCKED", "ISOLATED_READONLY_SCOPE_INVALID")
        try:
            document = await self._cli.list_tabs_readonly(
                isolated_namespace, timeout_seconds=timeout_seconds,
            )
        except Exception:
            return SafeReadOnlyChromeSummary("BLOCKED", "TAB_INVENTORY_UNAVAILABLE")
        if not isinstance(document, dict) or document.get("success") is not True:
            return SafeReadOnlyChromeSummary("BLOCKED", "TAB_INVENTORY_INVALID")
        data = document.get("data")
        tabs = data.get("tabs") if isinstance(data, dict) and data.get("full") is True else None
        if not isinstance(tabs, list) or len(tabs) > 32:
            return SafeReadOnlyChromeSummary("BLOCKED", "TAB_INVENTORY_INVALID")
        tab_ids: set[str] = set()
        target_ids: set[str] = set()
        home = blank = 0
        for tab in tabs:
            if not isinstance(tab, dict):
                return SafeReadOnlyChromeSummary("BLOCKED", "TAB_ENTRY_INVALID")
            tab_id, target = tab.get("tabId"), tab.get("targetId")
            if (
                not isinstance(tab_id, str) or not tab_id or len(tab_id) > 256
                or not isinstance(target, str) or not target or len(target) > 256
                or tab_id in tab_ids or target in target_ids
                or tab.get("ownership") != "created"
                or tab.get("relayAttached") is not True
                or tab.get("type") != "page"
            ):
                return SafeReadOnlyChromeSummary("BLOCKED", "TAB_OWNERSHIP_UNVERIFIED")
            tab_ids.add(tab_id)
            target_ids.add(target)
            url = tab.get("url")
            home += int(url in _HOME)
            blank += int(url == "about:blank")
        return SafeReadOnlyChromeSummary(
            "READONLY_COUNTS_UNATTESTED",
            "OWNED_TABS_NOT_AUTH_OR_GPT_FINAL_PROOF",
            owned_tabs=len(tabs),
            chatgpt_home_tabs=home,
            blank_tabs=blank,
        )
