"""No-send recovery fence tests: synthetic SQLite proofs ONLY, no host issuer."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import os
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.isolated_continuation_review_fence import (
    block_claimed_candidate_without_send,
    claim_candidate_for_no_send_review,
)
from master_a_dynamic_v4.isolated_host_replay_ledger import _SCHEMA
from master_a_dynamic_v4.isolated_verified_continuation_latch import _EXTRA_SCHEMA

PROJECT = "r2-review-fence-fixture"
DECISION = "decision-" + "d" * 64
KEY = "continue-" + "b" * 64
EVENT = "e" * 64
DIGEST = "a" * 64


class DurableNoSendReviewFenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name) / "experiments"
        self.root.mkdir()
        self.folder = self.root / "r2-review-fence-test"
        self.folder.mkdir()
        self.db = self.folder / "host-terminal-replay-ledger.sqlite3"

    def seed(self):
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.executescript(_SCHEMA + _EXTRA_SCHEMA)
            db.execute(
                "INSERT INTO host_terminal_events("
                "event_key,scope_key,event_sequence,receipt_sha256) VALUES(?,?,?,?)",
                (EVENT, "s"*64, 2, "f"*64),
            )
            db.execute(
                "INSERT INTO host_terminal_global_ids("
                "event_id_sha256,event_key) VALUES(?,?)",
                ("c"*64, EVENT),
            )
            db.execute(
                "INSERT INTO host_terminal_scope_cursors("
                "scope_key,high_sequence) VALUES(?,?)", ("s"*64, 2),
            )
            db.execute(
                "INSERT INTO host_continuation_candidates("
                "idempotency_key,event_key,project_id,decision_id,proof_sha256"
                ") VALUES(?,?,?,?,?)",
                (KEY, EVENT, PROJECT, DECISION, DIGEST),
            )
            db.commit()

    def claim(self, *, key=KEY, project=PROJECT, decision=DECISION,
              db=None):
        return claim_candidate_for_no_send_review(
            db or self.db, allowed_experiments_root=self.root,
            idempotency_key=key,
            expected_project_id=project, expected_decision_id=decision,
        )

    def block(self, *, key=KEY, project=PROJECT):
        return block_claimed_candidate_without_send(
            self.db, allowed_experiments_root=self.root,
            idempotency_key=key, expected_project_id=project,
        )

    def count(self):
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            return db.execute(
                "SELECT COUNT(*) FROM host_continuation_review_fences"
            ).fetchone()[0]

    def status(self):
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            return db.execute(
                "SELECT status FROM host_continuation_review_fences WHERE idempotency_key=?",
                (KEY,),
            ).fetchone()[0]

    def test_first_claim_is_for_review_only_not_browser_send(self):
        self.seed()
        result = self.claim()
        self.assertEqual("CLAIMED_FOR_REVIEW", result.status)
        self.assertTrue(result.candidate_claimed_for_review)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.local_execution_authorized)
        self.assertEqual(1, self.count())
        self.assertEqual("CLAIMED_FOR_REVIEW", self.status())

    def test_restarted_claim_is_not_reusable(self):
        self.seed()
        self.assertEqual("CLAIMED_FOR_REVIEW", self.claim().status)
        after_restart = self.claim()
        self.assertEqual("ALREADY_CLAIMED", after_restart.status)
        self.assertFalse(after_restart.candidate_claimed_for_review)
        self.assertFalse(after_restart.browser_send_authorized)
        self.assertEqual(1, self.count())

    def test_concurrent_duplicate_claims_have_one_winner(self):
        self.seed()
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: self.claim(), range(4)))
        self.assertEqual(1, sum(x.status == "CLAIMED_FOR_REVIEW" for x in results))
        self.assertEqual(3, sum(x.status == "ALREADY_CLAIMED" for x in results))
        self.assertTrue(all(not x.browser_send_authorized for x in results))
        self.assertEqual(1, self.count())

    def test_persistent_ambiguity_blocks_restart_forever(self):
        self.seed()
        self.claim()
        blocked = self.block()
        self.assertEqual("BLOCKED_AMBIGUOUS", blocked.status)
        self.assertEqual("BLOCKED_AMBIGUOUS", self.status())
        self.assertEqual("ALREADY_BLOCKED", self.block().status)
        self.assertEqual("ALREADY_CLAIMED", self.claim().status)
        self.assertFalse(self.claim().browser_send_authorized)

    def test_cannot_mark_unclaimed_candidate_ambiguous(self):
        self.seed()
        result = self.block()
        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("REVIEW_CLAIM_NOT_VERIFIED", result.reason)

    def test_cannot_claim_without_existing_verified_ledger(self):
        result = self.claim()
        self.assertEqual("VERIFIED_CANDIDATE_LEDGER_MISSING", result.reason)
        self.assertFalse(self.db.exists())

    def test_cannot_claim_unknown_candidate(self):
        self.seed()
        result = self.claim(key="continue-" + "0"*64)
        self.assertEqual("VERIFIED_CANDIDATE_NOT_FOUND", result.reason)
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            self.assertFalse(db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name='host_continuation_review_fences'"
            ).fetchone())

    def test_cannot_claim_with_wrong_project_or_decision(self):
        self.seed()
        for args in ({"project": "wrong"}, {"decision": "decision-bad"}):
            with self.subTest(args=args):
                result = self.claim(**args)
                self.assertEqual("DURABLE_DECISION_BINDING_MISMATCH", result.reason)
        self.assertEqual("CLAIMED_FOR_REVIEW", self.claim().status)

    def test_missing_global_event_does_not_claim(self):
        self.seed()
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("DELETE FROM host_terminal_global_ids")
            db.commit()
        result = self.claim()
        self.assertEqual("TERMINAL_EVENT_LEDGER_INCOMPLETE", result.reason)

    def test_sqlite_error_rolls_back_claim_without_contaminating_candidate(self):
        self.seed()
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.executescript("""
              CREATE TABLE host_continuation_review_fences(
                idempotency_key TEXT PRIMARY KEY,
                project_id TEXT,decision_id TEXT,proof_sha256 TEXT,status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                changed_at TEXT DEFAULT CURRENT_TIMESTAMP
              );
              CREATE TRIGGER fail_review BEFORE INSERT ON host_continuation_review_fences
              BEGIN SELECT RAISE(ABORT, 'synthetic crash'); END;
            """)
        result = self.claim()
        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("REVIEW_FENCE_LEDGER_UNAVAILABLE", result.reason)
        self.assertEqual(0, self.count())
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("DROP TRIGGER fail_review")
            db.commit()
        self.assertEqual("CLAIMED_FOR_REVIEW", self.claim().status)

    def test_production_path_and_frozen_observer_denied(self):
        self.seed()
        for folder in ("r2-gpt-session-audit-20261009",
                       "r2-observer-state-20261009"):
            f = self.root / folder
            f.mkdir()
            result = self.claim(db=f / "host-terminal-replay-ledger.sqlite3")
            self.assertEqual("EXPERIMENT_SCOPE_INVALID", result.reason)
        prod = self.root.parent / "production"
        prod.mkdir()
        self.assertEqual(
            "EXPERIMENT_SCOPE_INVALID",
            self.claim(db=prod / "host-terminal-replay-ledger.sqlite3").reason,
        )

    def test_hardlink_to_existing_fixture_is_never_mutated(self):
        self.seed()
        duplicate = self.root / "r2-hardlink-no-write"
        duplicate.mkdir()
        alias = duplicate / "host-terminal-replay-ledger.sqlite3"
        try:
            os.link(self.db, alias)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not support hardlinks here")
        result = self.claim(db=alias)
        self.assertEqual("EXPERIMENT_SCOPE_INVALID", result.reason)
        # Both names must be rejected while a hardlink exists; unlink the
        # temporary alias before performing an ordinary isolated claim.
        self.assertEqual("EXPERIMENT_SCOPE_INVALID", self.claim().reason)
        alias.unlink()
        self.assertEqual("CLAIMED_FOR_REVIEW", self.claim().status)

    def test_invalid_keys_fail_closed_without_db_access(self):
        self.seed()
        for key in ("", "continue-" + "G"*64, "decision-" + "a"*64, None):
            with self.subTest(key=key):
                self.assertEqual("CONTINUATION_KEY_INVALID", self.claim(key=key).reason)

    def test_cannot_block_other_project_claim(self):
        self.seed()
        self.claim()
        self.assertEqual("REVIEW_CLAIM_NOT_VERIFIED",
                         self.block(project="unrelated-project").reason)
        self.assertEqual("CLAIMED_FOR_REVIEW", self.status())


if __name__ == "__main__":
    unittest.main()
