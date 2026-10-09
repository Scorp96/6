from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import sqlite3
import tempfile
import unittest

from isolated_chrome_owned_session_recovery_v4 import (
    inspect_previously_owned_blank_session_readonly,
)

N = "scorp-r2-isolated-previous-owner-fixture"


class FakeCLI:
    def __init__(self, *, active=False, wake=True, tab_count=2,
                 ownership="created", url="about:blank", broken=False,
                 duplicate=False, failed=False):
        self.active = active
        self.wake = wake
        self.tab_count = tab_count
        self.ownership = ownership
        self.url = url
        self.broken = broken
        self.duplicate = duplicate
        self.failed = failed
        self.calls = []

    async def list_sessions_readonly(self, *, timeout_seconds=12):
        self.calls.append("session list")
        sessions = [{"name": "production-session"}]
        if self.active:
            sessions.append({"name": N})
        return {"ok": True, "sessions": sessions}

    async def list_tabs_readonly(self, session, *, timeout_seconds=12):
        self.calls.append("tab list")
        if session != N:
            raise AssertionError("MUST_NOT_INSPECT_OR_ADOPT_UNKNOWN_SESSION")
        if self.failed:
            raise RuntimeError("PRIVATE SECRET https://chatgpt.com/c/secret")
        if self.wake:
            self.active = True
        tabs = [{
            "tabId": "tab-" + str(i if not self.duplicate else 1),
            "targetId": "target-" + str(i if not self.duplicate else 1),
            "url": self.url,
            "type": "page",
            "relayAttached": True,
            "ownership": self.ownership,
        } for i in range(self.tab_count)]
        if self.broken:
            return {"success": False, "data": {"full": True, "tabs": tabs}}
        return {"success": True, "data": {"full": True, "tabs": tabs}}

    async def run_json(self, *args, **kwargs):
        raise AssertionError("MUST_NOT_EXECUTE_BROWSER_MUTATION")


class OwnedSessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "experiments"
        self.root.mkdir()
        self.folder = self.root / "r2-one-shot-blank-tab-recovery-fixture"
        self.folder.mkdir()
        self.ledger = self.folder / "blank-tab-attempts.sqlite3"

    def seed(self, *, state="OBSERVED_UNATTESTED"):
        with contextlib.closing(sqlite3.connect(self.ledger)) as db:
            db.execute("""
                CREATE TABLE one_shot_blank_tabs(
                    namespace TEXT PRIMARY KEY,
                    state TEXT NOT NULL
                )
            """)
            db.execute("INSERT INTO one_shot_blank_tabs VALUES(?,?)", (N,state))
            db.commit()

    def check(self, cli=None, *, namespace=N, ledger=None, expected=2):
        return asyncio.run(inspect_previously_owned_blank_session_readonly(
            cli if cli is not None else FakeCLI(),
            namespace=namespace,
            original_canary_ledger=ledger if ledger is not None else self.ledger,
            allowed_experiments_root=self.root,
            expected_created_blank_count=expected,
        ))

    def test_dormant_known_session_read_only_witness_lazily_reappears(self):
        self.seed()
        cli = FakeCLI(active=False, wake=True)
        result = self.check(cli)
        self.assertEqual("OWNED_BLANK_SESSION_OBSERVED_UNATTESTED", result.status)
        self.assertEqual(["session list", "tab list", "session list"], cli.calls)
        self.assertFalse(result.previously_active)
        self.assertTrue(result.active_after_observation)
        self.assertEqual(2, result.owned_blank_tabs_observed)
        self.assertTrue(result.read_only_tab_probe_attempted)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.host_terminal_event_verified)
        self.assertFalse(result.authenticated)
        self.assertFalse(result.tab_navigation_authorized)

    def test_already_live_owned_session_passive_observation(self):
        self.seed()
        result = self.check(FakeCLI(active=True))
        self.assertEqual("OWNED_BLANK_SESSION_OBSERVED_UNATTESTED", result.status)
        self.assertTrue(result.previously_active)

    def test_dormant_owned_tabs_but_daemon_not_listed_stays_blocked(self):
        self.seed()
        result = self.check(FakeCLI(wake=False))
        self.assertEqual("OWNED_TABS_OBSERVED_BUT_DAEMON_NOT_LISTED", result.reason)
        self.assertFalse(result.active_after_observation)
        self.assertFalse(result.browser_send_authorized)

    def test_missing_sqlite_receipt_blocks_prior_to_transport(self):
        cli = FakeCLI()
        result = self.check(cli)
        self.assertEqual("DURABLE_PREVIOUS_SESSION_PROOF_MISSING", result.reason)
        self.assertEqual([], cli.calls)
        self.assertFalse(self.ledger.exists())

    def test_only_real_observed_unattested_ledger_is_accepted(self):
        self.seed(state="ATTEMPT_RESERVED")
        cli = FakeCLI()
        self.assertEqual("DURABLE_PREVIOUS_SESSION_PROOF_MISSING",
                         self.check(cli).reason)
        self.assertEqual([], cli.calls)

    def test_unowned_adopted_foreign_and_navigated_tabs_fail(self):
        self.seed()
        for cli in (FakeCLI(ownership="adopted"),
                    FakeCLI(ownership="foreign"),
                    FakeCLI(url="https://chatgpt.com/"),
                    FakeCLI(duplicate=True)):
            with self.subTest(mode=cli.__dict__):
                r = self.check(cli)
                self.assertEqual("PREVIOUSLY_CREATED_TAB_GROUP_UNVERIFIED",r.reason)
                self.assertFalse(r.browser_send_authorized)

    def test_exact_owned_tab_count_is_mandatory(self):
        self.seed()
        self.assertEqual(
            "PREVIOUSLY_CREATED_TAB_GROUP_UNVERIFIED",
            self.check(FakeCLI(tab_count=1)).reason,
        )
        self.assertEqual(
            "OWNED_BLANK_SESSION_OBSERVED_UNATTESTED",
            self.check(FakeCLI(tab_count=1), expected=1).status,
        )

    def test_failed_or_exceptional_tab_read_has_no_retry(self):
        self.seed()
        cli = FakeCLI(failed=True)
        result = self.check(cli)
        self.assertEqual("OWNED_TAB_OBSERVATION_FAILED", result.reason)
        self.assertEqual(["session list", "tab list"], cli.calls)
        self.assertNotIn("SECRET", str(result))
        self.assertFalse(result.browser_send_authorized)

    def test_invalid_snapshot_never_activates(self):
        self.seed()
        r = self.check(FakeCLI(broken=True))
        self.assertEqual("PREVIOUSLY_CREATED_TAB_GROUP_UNVERIFIED", r.reason)
        self.assertFalse(r.browser_send_authorized)

    def test_protected_frozen_observer_folders_rejected(self):
        self.seed()
        for folder in ("r2-gpt-session-audit-20261009",
                       "r2-observer-state-20261009",
                       "r2-host-terminal-security-20261009"):
            with self.subTest(folder=folder):
                p = self.root / folder
                p.mkdir()
                bad = p / "blank-tab-attempts.sqlite3"
                bad.write_bytes(self.ledger.read_bytes())
                cli = FakeCLI()
                self.assertEqual("DURABLE_PREVIOUS_SESSION_PROOF_MISSING",
                                 self.check(cli,ledger=bad).reason)
                self.assertEqual([], cli.calls)

    def test_hardlink_is_not_treated_as_owned_receipt(self):
        self.seed()
        alias = self.folder / "alias"
        try:
            os.link(self.ledger, alias)
        except OSError:
            self.skipTest("OS does not permit hardlink")
        self.assertEqual(
            "DURABLE_PREVIOUS_SESSION_PROOF_MISSING",
            self.check(FakeCLI()).reason,
        )

    def test_scope_names_and_timeout_fail_prior_to_io(self):
        self.seed()
        for name in ("master", "", None, "worker-1"):
            with self.subTest(name=name):
                cli=FakeCLI()
                self.assertEqual("RECOVERY_SCOPE_INVALID",
                                 self.check(cli,namespace=name).reason)
                self.assertEqual([],cli.calls)

    def test_ledger_remains_byte_identical_after_passive_observation(self):
        self.seed()
        prior = self.ledger.read_bytes()
        r = self.check()
        self.assertEqual("OWNED_BLANK_SESSION_OBSERVED_UNATTESTED", r.status)
        self.assertEqual(prior, self.ledger.read_bytes())


if __name__ == "__main__":
    unittest.main()
