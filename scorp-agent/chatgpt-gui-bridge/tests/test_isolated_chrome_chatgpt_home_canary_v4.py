from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
import os
import pathlib
import sqlite3
import tempfile
import unittest

from isolated_chrome_chatgpt_home_canary_v4 import (
    inspect_and_create_one_homepage_tab_no_send,
)

NAME = "scorp-r2-isolated-home-page-canary-fixture"
ROOT = "https://chatgpt.com/"


def tab(i, *, url="about:blank", owner="created", attached=True):
    return {
        "tabId": "tab-" + str(i),
        "targetId": "target-" + str(i),
        "url": url,
        "ownership": owner,
        "relayAttached": attached,
        "type": "page",
    }


class Browser:
    def __init__(self, *, existing=None, created_url=ROOT,
                 success=True, failure=False, concurrent=False):
        self.tabs = list(existing if existing is not None else [tab(1), tab(2)])
        self.created_url = created_url
        self.success = success
        self.failure = failure
        self.concurrent = concurrent
        self.calls = []
        self.sessions = [{"name": "other"}, {"name": NAME}]
        self._next = 3

    async def list_sessions_readonly(self, *, timeout_seconds=12):
        self.calls.append("session list")
        return {"ok": True, "sessions": self.sessions}

    async def list_tabs_readonly(self, session, *, timeout_seconds=12):
        if session != NAME:
            raise AssertionError("MUST_NOT_OBSERVE_PRODUCTION_ORIGINAL_TAB")
        self.calls.append("tab list")
        return {"success": True, "data": {"full": True, "tabs": [
            dict(row) for row in self.tabs
        ]}}

    async def run_json(self, session, *args, timeout_seconds=12):
        self.calls.append("tab new")
        if session != NAME or args != ("tab", "new", ROOT):
            raise AssertionError("MUST_ONLY_NEW_CHATGPT_ROOT_TAB_NO_SEND")
        if self.failure:
            raise RuntimeError("secret account https://chatgpt.com/c/PRIVATE")
        i = self._next
        self._next += 1
        if self.success:
            self.tabs.append(tab(i, url=self.created_url))
            if self.concurrent:
                self.tabs.append(tab(self._next, url="about:blank"))
        return {"success": self.success, "data": {"tabId": "tab-" + str(i)}}


class OneShotHomeCanaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name) / "experiments"
        self.root.mkdir()
        self.folder = self.root / "r2-one-shot-blank-tab-canary-fixture"
        self.folder.mkdir()
        self.db = self.folder / "chatgpt-home-no-send-once.sqlite3"

    def run_probe(self, browser=None, *, db=None, namespace=NAME, allow=True):
        return asyncio.run(inspect_and_create_one_homepage_tab_no_send(
            browser if browser is not None else Browser(),
            namespace=namespace,
            ledger=db if db is not None else self.db,
            allowed_experiments_root=self.root,
            authorized_homepage_canary=allow,
        ))

    def test_root_tab_created_only_once_and_no_send_or_auth(self):
        cli = Browser()
        decision = self.run_probe(cli)
        self.assertEqual("HOME_TAB_PRESENT_UNATTESTED", decision.status)
        self.assertEqual(["session list", "tab list", "tab new", "tab list"], cli.calls)
        self.assertTrue(decision.command_reserved)
        self.assertTrue(decision.command_attempted)
        self.assertTrue(decision.new_tab_confirmed)
        self.assertTrue(decision.old_tabs_preserved)
        self.assertFalse(decision.browser_send_authorized)
        self.assertFalse(decision.prompt_submission_attempted)
        self.assertFalse(decision.host_terminal_verified)
        self.assertFalse(decision.authenticated)
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            row = conn.execute("SELECT status FROM homepage_once").fetchone()
        self.assertEqual(("ROOT_OBSERVED_UNATTESTED",), row)

    def test_retry_after_crash_or_success_is_permanently_fenced(self):
        self.assertEqual("HOME_TAB_PRESENT_UNATTESTED", self.run_probe().status)
        cli = Browser()
        decision = self.run_probe(cli)
        self.assertEqual("ALREADY_RESERVED_NO_RETRY", decision.reason)
        self.assertEqual(["session list", "tab list"], cli.calls)
        self.assertFalse(decision.command_attempted)

    def test_no_permission_has_no_browser_or_disk_side_effects(self):
        cli = Browser()
        decision = self.run_probe(cli, allow=False)
        self.assertEqual(
            "EXPLICIT_NO_SEND_HOME_CANARY_AUTHORIZATION_REQUIRED", decision.reason,
        )
        self.assertEqual([], cli.calls)
        self.assertFalse(self.db.exists())

    def test_missing_session_never_creates_tab(self):
        cli = Browser()
        cli.sessions = [{"name": "original"}]
        self.assertEqual("EXACT_PREEXISTING_SESSION_REQUIRED",
                         self.run_probe(cli).reason)
        self.assertFalse(self.db.exists())
        self.assertEqual(["session list"], cli.calls)

    def test_adopted_or_foreign_tab_cannot_be_precondition(self):
        for kind in ("adopted", "foreign"):
            with self.subTest(kind=kind):
                cli = Browser(existing=[tab(1, owner=kind)])
                self.assertEqual("OWNED_BLANK_TAB_BOUNDARY_UNVERIFIED",
                                 self.run_probe(cli).reason)
                self.assertEqual(["session list", "tab list"], cli.calls)
                self.assertFalse(self.db.exists())

    def test_already_navigated_tab_disqualifies_repeat(self):
        cli = Browser(existing=[tab(1, url="https://chatgpt.com/")])
        self.assertEqual("OWNED_BLANK_TAB_BOUNDARY_UNVERIFIED",
                         self.run_probe(cli).reason)
        self.assertFalse(self.db.exists())

    def test_ambiguous_transport_result_stays_fenced(self):
        cli = Browser(failure=True)
        result = self.run_probe(cli)
        self.assertEqual("HOME_TAB_CREATION_EFFECT_AMBIGUOUS", result.reason)
        self.assertNotIn("PRIVATE", str(result))
        self.assertEqual("ALREADY_RESERVED_NO_RETRY",
                         self.run_probe(Browser()).reason)

    def test_creation_false_result_is_never_reissued(self):
        result = self.run_probe(Browser(success=False))
        self.assertEqual("HOME_TAB_RESPONSE_NOT_VERIFIED", result.reason)
        self.assertTrue(result.command_reserved)
        self.assertFalse(result.browser_send_authorized)

    def test_redirect_to_a_conversation_is_not_success(self):
        result = self.run_probe(Browser(
            created_url="https://chatgpt.com/c/secret-old-master"
        ))
        self.assertEqual("HOME_URL_NOT_EXACT_OR_REDIRECTED", result.reason)
        self.assertNotIn("secret", str(result))
        self.assertFalse(result.new_tab_confirmed)

    def test_more_than_one_new_tab_is_ambiguous(self):
        result = self.run_probe(Browser(concurrent=True))
        self.assertEqual("EXACTLY_ONE_NEW_HOME_TAB_NOT_PROVEN", result.reason)

    def test_changed_old_tab_id_is_not_proven_preserved(self):
        class Changed(Browser):
            async def list_tabs_readonly(self, session, *, timeout_seconds=12):
                observed = await super().list_tabs_readonly(session, timeout_seconds=timeout_seconds)
                if "tab new" in self.calls:
                    observed["data"]["tabs"][0]["targetId"] = "unexpected-new-id"
                return observed
        result = self.run_probe(Changed())
        self.assertEqual("EXISTING_OWNED_TAB_CHANGED", result.reason)

    def test_unowned_new_tab_cannot_be_accepted(self):
        class NewForeign(Browser):
            async def run_json(self, *a, **kw):
                v = await super().run_json(*a, **kw)
                self.tabs[-1]["ownership"] = "foreign"
                return v
        self.assertEqual("HOME_TAB_OWNERSHIP_UNVERIFIED",
                         self.run_probe(NewForeign()).reason)

    def test_cannot_write_to_old_frozen_or_production_scope(self):
        for f in ("r2-gpt-session-audit-20261009",
                  "r2-observer-state-20261009",
                  "r2-host-terminal-security-20261009"):
            with self.subTest(folder=f):
                p = self.root / f
                p.mkdir()
                ledger = p / "chatgpt-home-no-send-once.sqlite3"
                result = self.run_probe(Browser(), db=ledger)
                self.assertEqual("ISOLATED_LEDGER_SCOPE_UNSAFE", result.reason)
                self.assertFalse(ledger.exists())

    def test_hardlinks_are_rejected(self):
        self.db.write_bytes(b"fake sqlite")
        alias = self.folder / "aliased"
        try:
            os.link(self.db, alias)
        except OSError:
            self.skipTest("hard links unsupported")
        self.assertEqual("ISOLATED_LEDGER_SCOPE_UNSAFE",
                         self.run_probe().reason)

    def test_concurrent_process_equivalent_one_browser_command(self):
        browser = Browser()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(lambda _: self.run_probe(browser), range(2)))
        self.assertEqual(1, browser.calls.count("tab new"))
        self.assertEqual(1, sum(x.command_reserved for x in outputs))
        self.assertTrue(all(not x.browser_send_authorized for x in outputs))


if __name__ == "__main__":
    unittest.main()
