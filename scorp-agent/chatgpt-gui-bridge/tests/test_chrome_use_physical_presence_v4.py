from __future__ import annotations

import asyncio
from dataclasses import replace
import unittest

from chrome_use_cli_v3 import ChromeUseCliV3
from chrome_use_physical_presence_v4 import inspect_pinned_chrome_tabs_readonly

URL = "https://chatgpt.com/c/read-only-owned-worker"


class FakeChromeUse:
    def __init__(self, *, session_name="pinned-session-1", tabs=None, inventory=None):
        self.session_name = session_name
        self.calls = []
        self.inventory = inventory if inventory is not None else {
            "ok": True,
            "sessions": [{"name": session_name, "owner": "test", "pid": 100}],
        }
        self.tabs = tabs if tabs is not None else {
            "success": True,
            "data": {
                "full": True,
                "tabs": [{
                    "tabId": "tab-1", "targetId": "target-1",
                    "url": URL, "title": "PRIVATE TITLE NEVER LEAK",
                    "type": "page", "ownership": "unverified",
                    "relayAttached": True, "active": True,
                }],
            },
        }

    async def list_sessions_readonly(self, *, timeout_seconds=10):
        self.calls.append(("sessions", timeout_seconds))
        if isinstance(self.inventory, Exception):
            raise self.inventory
        return self.inventory

    async def list_tabs_readonly(self, session, *, timeout_seconds=10):
        self.calls.append(("tabs", session, timeout_seconds))
        if isinstance(self.tabs, Exception):
            raise self.tabs
        return self.tabs


def check(fake=None, **kwargs):
    return asyncio.run(inspect_pinned_chrome_tabs_readonly(
        fake if fake is not None else FakeChromeUse(),
        expected_session=kwargs.pop("expected_session", "pinned-session-1"),
        expected_conversation_url=kwargs.pop("expected_conversation_url", URL),
        **kwargs,
    ))


class PhysicalTabPresenceTests(unittest.TestCase):
    def test_exact_live_tab_only_yields_unattested_candidate(self):
        cli = FakeChromeUse()
        result = check(cli)
        self.assertEqual("PRESENCE_CANDIDATE_UNATTESTED", result.status)
        self.assertEqual("EXACT_TAB_URL_MATCH_ONLY_NO_HOST_ATTESTATION", result.reason)
        self.assertEqual(1, result.session_count)
        self.assertEqual(1, result.matching_tab_count)
        self.assertFalse(result.host_terminal_event_verified)
        self.assertFalse(result.physical_binding_verified)
        self.assertFalse(result.auth_verified)
        self.assertFalse(result.browser_send_authorized)
        self.assertEqual([("sessions", 10), ("tabs", "pinned-session-1", 10)], cli.calls)
        self.assertNotIn(URL, str(result))
        self.assertNotIn("PRIVATE TITLE", str(result))

    def test_realistic_about_blank_tab_blocks_master_adoption(self):
        fake = FakeChromeUse(tabs={
            "success": True,
            "data": {"full": True, "tabs": [{
                "url": "about:blank", "active": True,
                "relayAttached": True, "type": "page",
            }]},
        })
        r = check(fake)
        self.assertEqual("BLOCKED", r.status)
        self.assertEqual("PINNED_CHATGPT_TAB_ABSENT", r.reason)
        self.assertEqual(0, r.matching_tab_count)

    def test_unknown_session_never_falls_back_to_the_only_session(self):
        cli = FakeChromeUse()
        r = check(cli, expected_session="other-master-session")
        self.assertEqual("PINNED_SESSION_MISSING", r.reason)
        self.assertEqual(1, len(cli.calls))
        self.assertEqual("sessions", cli.calls[0][0])

    def test_duplicate_pinned_session_identity_rejected(self):
        row = {"name": "pinned-session-1"}
        cli = FakeChromeUse(inventory={"ok": True, "sessions": [row, dict(row)]})
        self.assertEqual("DUPLICATE_SESSION_ID", check(cli).reason)
        self.assertEqual(1, len(cli.calls))

    def test_missing_or_unverified_full_tab_metadata_rejected(self):
        for data in (None, {"full": False, "tabs": []}, {"tabs": []}):
            with self.subTest(data=data):
                cli = FakeChromeUse(tabs={"success": True, "data": data})
                self.assertEqual("FULL_TAB_METADATA_REQUIRED", check(cli).reason)

    def test_previously_pinned_tab_needs_active_relay_attachment(self):
        for key in ("active", "relayAttached"):
            with self.subTest(key=key):
                cli = FakeChromeUse()
                cli.tabs["data"]["tabs"][0][key] = False
                r = check(cli)
                self.assertEqual("PINNED_TAB_NOT_ACTIVE_OR_ATTACHED", r.reason)
                self.assertFalse(r.browser_send_authorized)

    def test_multiple_chatgpt_tabs_fail_closed_even_if_expected_is_active(self):
        cli = FakeChromeUse()
        cli.tabs["data"]["tabs"].append({
            "url": "https://chatgpt.com/c/another-tab",
            "active": False, "relayAttached": True, "type": "page",
        })
        self.assertEqual("MULTIPLE_CHATGPT_TABS_UNVERIFIED", check(cli).reason)

    def test_duplicate_url_tabs_fail_closed(self):
        cli = FakeChromeUse()
        cli.tabs["data"]["tabs"].append(dict(cli.tabs["data"]["tabs"][0]))
        self.assertEqual("MULTIPLE_CHATGPT_TABS_UNVERIFIED", check(cli).reason)

    def test_unexpected_tab_type_or_unexpected_entry_rejected(self):
        cli = FakeChromeUse()
        cli.tabs["data"]["tabs"][0]["type"] = "service_worker"
        self.assertEqual("PINNED_TAB_TYPE_INVALID", check(cli).reason)
        cli.tabs["data"]["tabs"][0] = "malformed"
        self.assertEqual("TAB_ENTRY_INVALID", check(cli).reason)

    def test_invalid_pinned_url_and_timeout_never_call_browser(self):
        cli = FakeChromeUse()
        self.assertEqual(
            "PINNED_CONVERSATION_INVALID",
            check(cli, expected_conversation_url="about:blank").reason,
        )
        self.assertEqual("PINNED_SESSION_OR_TIMEOUT_INVALID", check(cli, timeout_seconds=0).reason)
        self.assertEqual([], cli.calls)

    def test_cli_transport_errors_and_secrets_never_return_in_reason(self):
        leaked = "PRIVATE https://chatgpt.com/c/sensitive-session"
        for error in (RuntimeError(leaked), ValueError(leaked)):
            cli = FakeChromeUse(inventory=error)
            result = check(cli)
            self.assertEqual("SESSION_INVENTORY_UNAVAILABLE", result.reason)
            self.assertNotIn(leaked, str(result))
            cli = FakeChromeUse(tabs=error)
            result = check(cli)
            self.assertEqual("TAB_INVENTORY_UNAVAILABLE", result.reason)
            self.assertNotIn(leaked, str(result))

    def test_full_list_adapter_invokes_only_read_only_chrome_commands(self):
        async def runner(argv, timeout_seconds):
            history.append(tuple(argv))
            if argv[-2:] == ["session", "list"]:
                return 0, '{"ok":true,"sessions":[{"name":"pinned-session-1"}]}', ""
            if argv[-3:] == ["tab", "list", "--full"]:
                return 0, '{"success":true,"data":{"full":true,"tabs":[]}}', ""
            raise AssertionError("unexpected CLI command")

        history = []
        cli = ChromeUseCliV3(executable="fixture-chrome-use", runner=runner)
        result = check(cli)
        self.assertEqual("PINNED_CHATGPT_TAB_ABSENT", result.reason)
        self.assertEqual([
            ("fixture-chrome-use", "--json", "session", "list"),
            ("fixture-chrome-use", "--session", "pinned-session-1", "--json",
             "tab", "list", "--full"),
        ], history)


if __name__ == "__main__":
    unittest.main()
