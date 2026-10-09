from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
import os
import pathlib
import sqlite3
import tempfile
import unittest

from isolated_chrome_blank_tab_canary_v4 import attempt_one_blank_tab_canary

NAME = "scorp-r2-isolated-canary-blank-fixture"
TAB = "new-blank-tab-only"


class FakeCLI:
    def __init__(self, *, create=True, list_after=True, accepted=True,
                 url="about:blank", attached=True, bad_tab=False, error=None):
        self.create = create
        self.list_after = list_after
        self.accepted = accepted
        self.url = url
        self.attached = attached
        self.bad_tab = bad_tab
        self.error = error
        self.names = {"production-owner"}
        self.calls = []

    async def list_sessions_readonly(self, *, timeout_seconds=15):
        self.calls.append("session list")
        return {"ok": True, "sessions": [{"name": n} for n in sorted(self.names)]}

    async def run_json(self, namespace, *args, timeout_seconds=15):
        self.calls.append("tab new")
        if namespace != NAME or args != ("tab", "new", "about:blank"):
            raise AssertionError("MUST_ONLY_CREATE_ISOLATED_BLANK_TAB")
        if self.error:
            raise RuntimeError("private-data https://chatgpt.com/c/private")
        if self.create and self.list_after:
            self.names.add(NAME)
        return {"success": self.accepted, "data": {"tabId": TAB}}

    async def list_tabs_readonly(self, session, *, timeout_seconds=15):
        self.calls.append("tab list")
        if session != NAME:
            raise AssertionError("PRODUCTION_SESSION_READ_NOT_PERMITTED")
        tabs = [{
            "tabId": TAB if not self.bad_tab else "wrong-id",
            "url": self.url, "type": "page", "relayAttached": self.attached,
            "ownership": "untrusted", "active": False,
        }]
        return {"success": True, "data": {"full": True, "tabs": tabs}}


class BlankTabCanaryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name)
        self.folder = self.root / "r2-one-shot-blank-tab-fixture"
        self.folder.mkdir()
        self.db = self.folder / "blank-tab-attempts.sqlite3"

    def run_canary(self, cli=None, *, name=NAME, allow=True, db=None):
        return asyncio.run(attempt_one_blank_tab_canary(
            cli if cli is not None else FakeCLI(), namespace=name,
            ledger=db if db is not None else self.db,
            allowed_experiments_root=self.root,
            allow_new_blank_tab=allow,
        ))

    def test_success_is_unattested_and_no_gpt_send_or_navigation(self):
        cli = FakeCLI()
        r = self.run_canary(cli)
        self.assertEqual("BLANK_TAB_CANDIDATE_UNATTESTED", r.status)
        self.assertEqual(
            ["session list", "tab new", "session list", "tab list"], cli.calls,
        )
        self.assertTrue(r.attempt_reserved)
        self.assertTrue(r.command_accepted)
        self.assertTrue(r.new_blank_tab_confirmed)
        self.assertFalse(r.browser_send_authorized)
        self.assertFalse(r.chatgpt_navigation_authorized)
        self.assertFalse(r.host_terminal_event_verified)
        self.assertFalse(r.physical_profile_isolation_verified)
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            state = conn.execute("SELECT state FROM one_shot_blank_tabs").fetchone()[0]
        self.assertEqual("OBSERVED_UNATTESTED", state)

    def test_no_permission_never_touches_disk_or_browser(self):
        cli = FakeCLI()
        r = self.run_canary(cli, allow=False)
        self.assertEqual("EXPLICIT_BLANK_TAB_CANARY_PERMISSION_REQUIRED", r.reason)
        self.assertFalse(self.db.exists())
        self.assertEqual([], cli.calls)

    def test_second_try_never_replays_prior_tab_creation(self):
        cli = FakeCLI()
        self.assertEqual("BLANK_TAB_CANDIDATE_UNATTESTED", self.run_canary(cli).status)
        other = FakeCLI()
        r = self.run_canary(other)
        self.assertEqual("ALREADY_RESERVED_NO_RETRY", r.reason)
        self.assertEqual(["session list"], other.calls)

    def test_failed_create_side_effect_is_not_retried(self):
        cli = FakeCLI(error=True)
        r = self.run_canary(cli)
        self.assertEqual("BLANK_TAB_CREATE_OUTCOME_UNKNOWN", r.reason)
        self.assertEqual("ALREADY_RESERVED_NO_RETRY",
                         self.run_canary(FakeCLI()).reason)
        self.assertNotIn("private-data", str(r))

    def test_return_ok_without_registered_session_blocks(self):
        r = self.run_canary(FakeCLI(list_after=False))
        self.assertEqual("NEW_SESSION_NOT_VISIBLE", r.reason)
        self.assertFalse(r.chatgpt_navigation_authorized)

    def test_not_attached_blank_tab_fails_closed(self):
        self.assertEqual("BLANK_TAB_PHYSICAL_PROPERTIES_UNVERIFIED",
                         self.run_canary(FakeCLI(attached=False)).reason)

    def test_mismatching_tab_id_is_not_adopted(self):
        self.assertEqual("CREATED_TAB_NOT_UNIQUELY_VISIBLE",
                         self.run_canary(FakeCLI(bad_tab=True)).reason)

    def test_chatgpt_url_is_unacceptable_in_blank_only_canary(self):
        r = self.run_canary(FakeCLI(url="https://chatgpt.com/c/private"))
        self.assertEqual("BLANK_TAB_PHYSICAL_PROPERTIES_UNVERIFIED", r.reason)
        self.assertNotIn("private", str(r))

    def test_native_command_return_failure_cannot_be_ignored(self):
        r = self.run_canary(FakeCLI(accepted=False))
        self.assertEqual("BLANK_TAB_CREATE_NOT_CONFIRMED", r.reason)

    def test_already_existing_namespace_does_not_create_tab(self):
        cli = FakeCLI()
        cli.names.add(NAME)
        r = self.run_canary(cli)
        self.assertEqual("PRE_CREATION:ISOLATED_NAMESPACE_ALREADY_EXISTS", r.reason)
        self.assertEqual(["session list"], cli.calls)
        self.assertFalse(self.db.exists())

    def test_malformed_namespace_never_touches_disk(self):
        cli = FakeCLI()
        self.assertEqual("ISOLATED_NAMESPACE_INVALID",
                         self.run_canary(cli, name="master").reason)
        self.assertEqual([], cli.calls)
        self.assertFalse(self.db.exists())

    def test_sqlite_file_hardlink_not_writable(self):
        self.db.write_bytes(b"not-a-database")
        link = self.folder / "alias.db"
        try:
            os.link(self.db, link)
        except OSError:
            self.skipTest("hardlinks not supported")
        self.assertEqual("ISOLATED_LEDGER_PATH_UNSAFE",
                         self.run_canary(FakeCLI()).reason)

    def test_symlink_directory_escape_refused(self):
        outsider = self.root / "outsider"
        outsider.mkdir()
        alias = self.root / "r2-symlink"
        try:
            alias.symlink_to(outsider, target_is_directory=True)
        except OSError:
            self.skipTest("symlink not supported")
        db = alias / "blank-tab-attempts.sqlite3"
        self.assertEqual("ISOLATED_LEDGER_PATH_UNSAFE",
                         self.run_canary(FakeCLI(), db=db).reason)

    def test_concurrent_one_shot_calls_make_only_one_tab(self):
        # Synthetic concurrency with a shared fake CLI inventory. A winner
        # might create before the loser preflight; either path is fail-closed.
        cli = FakeCLI()
        def once(_):
            return self.run_canary(cli)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(once, range(2)))
        self.assertEqual(1, sum(x.attempt_reserved for x in outcomes))
        self.assertEqual(1, cli.calls.count("tab new"))
        self.assertTrue(all(not x.browser_send_authorized for x in outcomes))


if __name__ == "__main__":
    unittest.main()
