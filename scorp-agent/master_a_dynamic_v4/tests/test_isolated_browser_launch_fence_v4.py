"""Isolated one-shot browser *intent* ledger, NEVER actual ChatGPT send tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.isolated_browser_launch_fence_v4 import (
    read_attempt_state,
    record_host_review_only,
    reserve_external_launch_edge_once,
    reserve_isolated_browser_launch,
)

_ATTEMPT = "a" * 32
_NAME = "scorp-r2-worker2-fresh-launch-sandbox-20261009"


class IsolatedBrowserLaunchFenceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name) / "experiments"
        self.root.mkdir()
        self.sandbox = self.root / "r2-browser-launch-sandbox-20261009"
        self.sandbox.mkdir()
        self.db = self.sandbox / "launch_attempts.sqlite3"
        self.arguments = dict(
            database=self.db, experiments_root=self.root,
            attempt_id=_ATTEMPT, namespace=_NAME,
        )

    def reserve(self, **changes):
        kwargs = dict(self.arguments)
        kwargs["target_origin"] = "https://chatgpt.com"
        kwargs.update(changes)
        return reserve_isolated_browser_launch(**kwargs)

    def edge(self, **changes):
        kwargs = dict(self.arguments)
        kwargs.update(changes)
        return reserve_external_launch_edge_once(**kwargs)

    def review(self, status, **changes):
        kwargs = dict(self.arguments)
        kwargs.update(changes)
        return record_host_review_only(**kwargs, observed_status=status)

    def test_first_reservation_writes_exactly_one_durable_intent(self):
        r = self.reserve()
        self.assertEqual("RESERVED", r.status)
        self.assertTrue(r.attempt_recorded)
        self.assertFalse(r.external_edge_reserved)
        self.assertFalse(r.browser_or_model_send_authorized)
        self.assertFalse(r.original_master_resume_authorized)
        self.assertFalse(r.independent_gpt_worker_authenticated)
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM browser_launch_attempts").fetchone()[0])
            self.assertEqual("ok", db.execute("PRAGMA quick_check").fetchone()[0])

    def test_external_launch_edge_can_be_reserved_exactly_once(self):
        self.reserve()
        self.assertEqual("EXTERNAL_EDGE_RESERVED", self.edge().status)
        repeated = self.edge()
        self.assertEqual("BLOCKED", repeated.status)
        self.assertEqual("LAUNCH_EDGE_ALREADY_CONSUMED_OR_UNKNOWN", repeated.reason)
        state = read_attempt_state(**self.arguments)
        self.assertEqual("EXTERNAL_LAUNCH_EDGE_RESERVED", state.reason)
        self.assertFalse(state.browser_or_model_send_authorized)

    def test_crash_after_external_edge_must_not_replay_browser_open(self):
        self.reserve()
        self.edge()
        # Simulate process crash: re-enter with same immutable attempt.
        self.assertEqual("BLOCKED", self.reserve().status)
        self.assertEqual("BLOCKED", self.edge().status)
        self.assertEqual("REVIEW_ONLY", read_attempt_state(**self.arguments).status)

    def test_unknown_launch_timeout_never_grants_retry(self):
        self.reserve()
        self.edge()
        self.assertEqual("RECORDED_FOR_REVIEW", self.review("LAUNCH_TIMED_OUT_UNVERIFIED").status)
        self.assertEqual("BLOCKED", self.edge().status)
        self.assertEqual("BLOCKED", self.reserve().status)
        self.assertEqual("LAUNCH_TIMED_OUT_UNVERIFIED", read_attempt_state(**self.arguments).reason)

    def test_stop_confirmation_marks_review_only_and_never_authenticates(self):
        self.reserve()
        self.edge()
        self.review("LAUNCH_TIMED_OUT_UNVERIFIED")
        result = self.review("ISOLATED_SESSION_STOPPED_FOR_REVIEW")
        self.assertEqual("RECORDED_FOR_REVIEW", result.status)
        self.assertFalse(result.browser_or_model_send_authorized)
        self.assertFalse(result.independent_gpt_worker_authenticated)
        self.assertEqual("BLOCKED", self.edge().status)

    def test_reusing_same_attempt_with_different_namespace_blocked(self):
        self.reserve()
        r = self.reserve(namespace="scorp-r2-worker2-different-launch-sandbox-20261009")
        self.assertEqual("EXISTING_OR_AMBIGUOUS_ATTEMPT_NO_REPLAY", r.reason)

    def test_reusing_same_namespace_with_different_attempt_blocked(self):
        self.reserve()
        r = self.reserve(attempt_id="b" * 32)
        self.assertEqual("EXISTING_OR_AMBIGUOUS_ATTEMPT_NO_REPLAY", r.reason)

    def test_target_origin_must_be_exact_homepage_origin(self):
        for url in ("https://chatgpt.com/c/abc", "https://chatgpt.com.evil.test",
                    "http://chatgpt.com", "https://example.org", "", None):
            with self.subTest(url=url):
                self.assertEqual("TARGET_ORIGIN_NOT_ALLOWLISTED",
                                 self.reserve(target_origin=url).reason)
        self.assertFalse(self.db.exists())

    def test_invalid_attempts_are_blocked_before_database_creation(self):
        for attempt in ("", "a" * 31, "z" * 32, "A" * 32, None, 123):
            with self.subTest(attempt=attempt):
                self.assertEqual("ATTEMPT_ID_INVALID",
                                 self.reserve(attempt_id=attempt).reason)
        self.assertFalse(self.db.exists())

    def test_invalid_namespace_is_blocked_before_database_creation(self):
        for name in ("master", "scorp-r2-worker2-short",
                     "scorp-r2-worker2-fresh-ABCD-20261009", None, 12):
            with self.subTest(name=name):
                self.assertEqual("NAMESPACE_INVALID",
                                 self.reserve(namespace=name).reason)
        self.assertFalse(self.db.exists())

    def test_production_or_arbitrary_paths_are_not_allowed(self):
        bad = self.root / "production" / "state.sqlite3"
        bad.parent.mkdir()
        r = self.reserve(database=bad)
        self.assertEqual("ISOLATED_EXPERIMENT_DATABASE_SCOPE_INVALID", r.reason)
        self.assertFalse(bad.exists())

    def test_read_only_state_query_does_not_create_database(self):
        result = read_attempt_state(**self.arguments)
        self.assertEqual("LEDGER_NOT_FOUND", result.reason)
        self.assertFalse(self.db.exists())

    def test_no_review_before_external_boundary(self):
        self.reserve()
        self.assertEqual("REVIEW_TRANSITION_NOT_AUTHORIZED",
                         self.review("COMMAND_RETURNED_UNVERIFIED").reason)

    def test_invalid_review_status_rejected(self):
        self.reserve()
        self.edge()
        for status in ("TURN_FINAL_CONFIRMED", "HOST_VERIFIED", "READY_TO_SEND", "",
                       "LAUNCH_CONFIRMED", None):
            with self.subTest(status=status):
                self.assertEqual("REVIEW_STATUS_NOT_ALLOWLISTED",
                                 self.review(status).reason)

    def test_duplicate_terminal_review_cannot_be_used_to_reopen(self):
        self.reserve()
        self.edge()
        self.review("COMMAND_RETURNED_UNVERIFIED")
        self.assertEqual("REVIEW_OUTCOME_ALREADY_RECORDED",
                         self.review("COMMAND_RETURNED_UNVERIFIED").reason)
        self.assertEqual("BLOCKED", self.edge().status)

    def test_illegal_transition_from_stopped_back_to_attempt_is_blocked(self):
        self.reserve()
        self.edge()
        self.review("ISOLATED_SESSION_STOPPED_FOR_REVIEW")
        self.assertEqual("REVIEW_TRANSITION_NOT_AUTHORIZED",
                         self.review("LAUNCH_TIMED_OUT_UNVERIFIED").reason)

    def test_multi_thread_race_stages_only_one_attempt(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda _: self.reserve(), range(4)))
        self.assertEqual(1, sum(x.status == "RESERVED" for x in answers))
        self.assertEqual(3, sum(x.status == "BLOCKED" for x in answers))
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM browser_launch_attempts").fetchone()[0])
            self.assertEqual("ok", db.execute("PRAGMA quick_check").fetchone()[0])

    def test_corrupted_existing_database_not_autorepaired(self):
        self.db.write_bytes(b"corrupt database")
        before = self.db.read_bytes()
        r = self.reserve()
        self.assertEqual("BLOCKED", r.status)
        self.assertEqual(before, self.db.read_bytes())

    def test_symlinked_database_or_parent_blocked(self):
        target = self.sandbox / "real.sqlite3"
        target.write_bytes(b"legacy")
        try:
            self.db.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual("ISOLATED_EXPERIMENT_DATABASE_SCOPE_INVALID",
                         self.reserve().reason)

    def test_fingerprint_and_read_only_return_without_secrets(self):
        self.reserve()
        result = read_attempt_state(**self.arguments)
        raw = json.dumps(result.__dict__)
        self.assertNotIn(_NAME, raw)
        self.assertNotIn(_ATTEMPT, raw)
        self.assertNotIn(str(self.db), raw)
        self.assertFalse(result.browser_or_model_send_authorized)

    def test_windows_file_lock_released_after_reserve_and_read(self):
        self.assertEqual("RESERVED", self.reserve().status)
        self.assertEqual("REVIEW_ONLY", read_attempt_state(**self.arguments).status)
        renamed = self.db.with_suffix(".bak")
        self.db.rename(renamed)
        renamed.rename(self.db)
        self.assertEqual("RESERVED_NO_EXTERNAL_ACTION",
                         read_attempt_state(**self.arguments).reason)

    def test_windows_file_lock_released_after_edge_and_review(self):
        self.reserve()
        self.edge()
        self.review("LAUNCH_TIMED_OUT_UNVERIFIED")
        renamed = self.db.with_suffix(".bak")
        self.db.rename(renamed)
        renamed.rename(self.db)
        self.assertEqual("LAUNCH_TIMED_OUT_UNVERIFIED",
                         read_attempt_state(**self.arguments).reason)

    def test_denied_replay_also_closes_windows_file_handles(self):
        self.reserve()
        self.edge()
        self.assertEqual("BLOCKED", self.edge().status)
        self.assertEqual("BLOCKED", self.reserve().status)
        renamed = self.db.with_suffix(".bak")
        self.db.rename(renamed)
        renamed.rename(self.db)
        self.assertEqual("EXTERNAL_LAUNCH_EDGE_RESERVED",
                         read_attempt_state(**self.arguments).reason)

    def test_no_browser_or_master_send_interfaces_in_module(self):
        import master_a_dynamic_v4.isolated_browser_launch_fence_v4 as module
        for name in ("open_browser", "submit_prompt", "wake_master",
                     "resume_original_r1", "send_to_chatgpt", "kill_chrome"):
            self.assertFalse(hasattr(module, name))


if __name__ == "__main__":
    unittest.main()
