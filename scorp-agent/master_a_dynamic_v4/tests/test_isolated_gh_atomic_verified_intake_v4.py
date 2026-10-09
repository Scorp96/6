"""Prove all-or-nothing intake after real pinned comment/blob validation."""
from __future__ import annotations

import contextlib
import hashlib
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.isolated_gh_atomic_verified_intake_v4 import (
    stage_atomic_pinned_substantive_artifact_for_review as stage_atomic,
)
from master_a_dynamic_v4.isolated_gh_verified_result_latch_v4 import (
    _SCHEMA as _BLOB_SCHEMA, _SUBSTANTIVE_SCHEMA,
)
from master_a_dynamic_v4.isolated_github_work_result_inbox_v4 import (
    _SCHEMA as _BASE_SCHEMA,
)
from master_a_dynamic_v4.isolated_two_verified_blobs_review_v4 import (
    inspect_two_git_verified_worker_results,
)
from test_isolated_gh_substantive_work_v2 import (
    make_artifact, PROJECT, ISSUE, AUTHOR, COMMIT,
    ASG, TASK, ASSIGN_SHA, PATH, CID,
)


class AtomicVerifiedIntakeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=pathlib.Path(temp.name)/"experiments"
        self.root.mkdir()
        folder=self.root/"r2-work-artifact-review-atomic-unit"
        folder.mkdir()
        self.database=folder/"work-artifact-review.sqlite3"

    def intake(self, i=0, **overrides):
        blob,comment,digest=make_artifact(i)
        kwargs=dict(
            database=self.database,allowed_experiments_root=self.root,
            comment_id=CID[i],expected_issue_number=ISSUE,
            allowlisted_github_author_id=AUTHOR,
            expected_project_id=PROJECT,expected_assignment_id=ASG[i],
            expected_task_id=TASK[i],expected_worker_slot="worker-slot-"+str(i+1),
            expected_assignment_sha256=ASSIGN_SHA[i],expected_state_version=0,
            expected_artifact_sha256=digest,artifact_commit_sha=COMMIT,
            artifact_path=PATH[i],
            comment_reader=lambda *_:comment,blob_reader=lambda *_:blob,
        )
        kwargs.update(overrides)
        return stage_atomic(**kwargs)

    def rows(self):
        if not self.database.exists():
            return None
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            return tuple(c.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
                         for table in (
                             "r2_github_review_artifacts",
                             "r2_immutable_artifact_attestations",
                             "r2_substantive_work_receipts"))

    def barrier(self):
        return inspect_two_git_verified_worker_results(
            barrier_kwargs=dict(
                database=self.database,allowed_experiments_root=self.root,
                expected_project_id=PROJECT,expected_state_version=0,
                worker_1_assignment_id=ASG[0],
                worker_1_assignment_sha256=ASSIGN_SHA[0],
                worker_1_issue_number=ISSUE,worker_1_author_id=AUTHOR,
                worker_2_assignment_id=ASG[1],
                worker_2_assignment_sha256=ASSIGN_SHA[1],
                worker_2_issue_number=ISSUE,worker_2_author_id=AUTHOR,
            ),
            first_commit_sha=COMMIT,first_artifact_path=PATH[0],
            second_commit_sha=COMMIT,second_artifact_path=PATH[1],
            require_substantive_work_product=True,
        )

    def test_first_atomic_commit_contains_exact_three_receipts(self):
        r=self.intake()
        self.assertEqual("ATOMIC_PINNED_SUBSTANTIVE_WORK_FOR_HUMAN_REVIEW",r.status)
        self.assertTrue(r.atomic_evidence_recorded)
        self.assertFalse(r.actual_worker_identity_verified)
        self.assertFalse(r.host_terminal_event_attested)
        self.assertFalse(r.browser_send_authorized)
        self.assertEqual((1,1,1),self.rows())
        self.assertEqual("AWAITING_SCOPED_BASE_RESULTS",self.barrier().status)

    def test_two_atomic_comments_produce_review_only_not_master_wake(self):
        self.intake(0)
        self.intake(1)
        self.assertEqual((2,2,2),self.rows())
        result=self.barrier()
        self.assertEqual("BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW",result.status)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.wake_master_authorized)
        self.assertFalse(result.actual_worker_identity_verified)
        with contextlib.closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual("ok",conn.execute("PRAGMA quick_check").fetchone()[0])

    def test_repeat_across_independent_calls_cannot_insert_a_second_row(self):
        self.intake(0)
        r=self.intake(0)
        self.assertEqual("BLOCKED",r.status)
        self.assertEqual("EXISTING_OR_ORPHANED_COMMENT_REVIEW_NO_REPLAY",r.reason)
        self.assertEqual((1,1,1),self.rows())

    def test_injected_failure_between_base_and_blob_rolls_back_everything(self):
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.executescript(_BASE_SCHEMA)
            db.executescript(_BLOB_SCHEMA)
            db.executescript(_SUBSTANTIVE_SCHEMA)
            db.execute("""
                CREATE TRIGGER deny_blob BEFORE INSERT
                ON r2_immutable_artifact_attestations BEGIN
                    SELECT RAISE(ABORT,'injected');
                END;
            """)
            db.commit()
        r=self.intake()
        self.assertEqual("BLOCKED",r.status)
        self.assertEqual("ATOMIC_REVIEW_LEDGER_FAILED_CLOSED",r.reason)
        self.assertEqual((0,0,0),self.rows())

    def test_injected_failure_after_blob_rolls_back_all_receipts(self):
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            for schema in (_BASE_SCHEMA,_BLOB_SCHEMA,_SUBSTANTIVE_SCHEMA):
                db.executescript(schema)
            db.execute("""
                CREATE TRIGGER deny_text BEFORE INSERT
                ON r2_substantive_work_receipts BEGIN
                    SELECT RAISE(ABORT,'injected');
                END;
            """)
            db.commit()
        r=self.intake()
        self.assertEqual("ATOMIC_REVIEW_LEDGER_FAILED_CLOSED",r.reason)
        self.assertEqual((0,0,0),self.rows())

    def test_orphan_legacy_base_row_is_not_automatically_repaired(self):
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM r2_immutable_artifact_attestations")
            db.execute("DELETE FROM r2_substantive_work_receipts")
            db.commit()
        r=self.intake(0)
        self.assertEqual("EXISTING_OR_ORPHANED_COMMENT_REVIEW_NO_REPLAY",r.reason)
        self.assertEqual((1,0,0),self.rows())

    def test_wrong_issue_is_refused_before_database_exists(self):
        r=self.intake(expected_issue_number=2451)
        self.assertEqual("SCOPED_COMMENT_VALIDATION_FAILED",r.reason)
        self.assertFalse(self.database.exists())

    def test_wrong_author_refused_no_sqlite_creation(self):
        r=self.intake(allowlisted_github_author_id=AUTHOR+1)
        self.assertEqual("SCOPED_COMMENT_VALIDATION_FAILED",r.reason)
        self.assertFalse(self.database.exists())

    def test_mismatched_file_digest_is_blocked_without_db(self):
        r=self.intake(expected_artifact_sha256="f"*64)
        self.assertEqual("COMMENT_FILE_DIGEST_MISMATCH",r.reason)
        self.assertFalse(self.database.exists())

    def test_metadata_only_v1_does_not_create_partial_claim(self):
        _,comment,digest=make_artifact(0,v2=False)
        blob,_,_=make_artifact(0,v2=False)
        r=self.intake(0,comment_reader=lambda *_:comment,
                      blob_reader=lambda *_:blob,expected_artifact_sha256=digest)
        self.assertEqual("PINNED_SUBSTANTIVE_BLOB_NOT_VERIFIED",r.reason)
        self.assertFalse(self.database.exists())

    def test_wrong_assignment_generation_cannot_claim(self):
        r=self.intake(expected_state_version=1)
        self.assertEqual("PINNED_SUBSTANTIVE_BLOB_NOT_VERIFIED",r.reason)
        self.assertFalse(self.database.exists())

    def test_preexisting_missing_blob_blocks_other_worker_without_ledger_change(self):
        self.assertTrue(self.intake(0).atomic_evidence_recorded)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM r2_immutable_artifact_attestations WHERE comment_id=?",(CID[0],))
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("BLOCKED",result.status)
        self.assertEqual("PREEXISTING_REVIEW_EVIDENCE_INCOMPLETE",result.reason)
        self.assertEqual(before,self.rows())

    def test_preexisting_missing_work_text_blocks_other_worker(self):
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM r2_substantive_work_receipts WHERE comment_id=?",(CID[0],))
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("PREEXISTING_REVIEW_EVIDENCE_INCOMPLETE",result.reason)
        self.assertEqual(before,self.rows())

    def test_preexisting_blob_content_mismatch_blocks_other_worker(self):
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE r2_immutable_artifact_attestations "
                       "SET artifact_sha256=? WHERE comment_id=?",("f"*64,CID[0]))
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("PREEXISTING_REVIEW_EVIDENCE_INCOMPLETE",result.reason)
        self.assertEqual(before,self.rows())

    def test_preexisting_slot_2_occupancy_blocks_new_slot_2_even_if_assignment_differs(self):
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE r2_github_review_artifacts SET worker_slot=? "
                       "WHERE comment_id=?",("worker-slot-2",CID[0]))
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("PROJECT_WORKER_SLOT_ALREADY_FILLED",result.reason)
        self.assertEqual(before,self.rows())

    def test_preexisting_identical_malformed_base_and_blob_hashes_block_second(self):
        # Equality alone is insufficient if both legacy digest fields were
        # corrupted to the SAME non-SHA256 string.
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE r2_github_review_artifacts "
                       "SET artifact_sha256=? WHERE comment_id=?",("not-a-sha",CID[0]))
            db.execute("UPDATE r2_immutable_artifact_attestations "
                       "SET artifact_sha256=? WHERE comment_id=?",("not-a-sha",CID[0]))
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("PREEXISTING_REVIEW_EVIDENCE_INCOMPLETE",result.reason)
        self.assertEqual(before,self.rows())

    def test_preexisting_corrupt_text_digest_blocks_other_worker(self):
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE r2_substantive_work_receipts "
                       "SET deliverable_sha256=? WHERE comment_id=?",("invalid",CID[0]))
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("PREEXISTING_REVIEW_EVIDENCE_INCOMPLETE",result.reason)
        self.assertEqual(before,self.rows())

    def test_unscoped_legacy_blob_receipt_refuses_new_worker_intake(self):
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "INSERT INTO r2_immutable_artifact_attestations "
                "(comment_id,artifact_commit_sha,artifact_path_digest,"
                "artifact_sha256,status) VALUES(?,?,?,?,?)",
                (9999999,"a"*40,"b"*64,"c"*64,
                 "GIT_BLOB_VERIFIED_FOR_HUMAN_REVIEW"),
            )
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("UNSCOPED_LEGACY_REVIEW_ORPHAN_PRESENT",result.reason)
        self.assertEqual(before,self.rows())

    def test_unscoped_legacy_work_text_receipt_refuses_new_worker_intake(self):
        self.intake(0)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "INSERT INTO r2_substantive_work_receipts "
                "(comment_id,deliverable_sha256,status) VALUES(?,?,?)",
                (9999998,"d"*64,"SUBSTANTIVE_TEXT_PRESENT_UNREVIEWED"),
            )
            db.commit()
        before=self.rows()
        result=self.intake(1)
        self.assertEqual("UNSCOPED_LEGACY_REVIEW_ORPHAN_PRESENT",result.reason)
        self.assertEqual(before,self.rows())

    def test_database_scope_restricts_legacy_production_path(self):
        production=pathlib.Path(self.root.parent)/"runtime-v4"/"active"
        production.mkdir(parents=True)
        attempt=production/"work-artifact-review.sqlite3"
        r=self.intake(database=attempt)
        self.assertEqual("ATOMIC_EXPERIMENT_SCOPE_INVALID",r.reason)
        self.assertFalse(attempt.exists())

    def test_comment_reader_failure_never_logs_private_message(self):
        def raise_secret(_):
            raise RuntimeError("SECRET SESSION TOKEN https://chatgpt.com/c/private")
        r=self.intake(comment_reader=raise_secret)
        self.assertEqual("GITHUB_COMMENT_READER_UNAVAILABLE",r.reason)
        self.assertNotIn("SECRET",repr(r))
        self.assertFalse(self.database.exists())

    def test_invalid_comment_body_fails_closed(self):
        _,comment,_=make_artifact(0)
        comment["body"]="SCORP_R2_WORK_ARTIFACT::{bad}"
        r=self.intake(comment_reader=lambda *_:comment)
        self.assertEqual("GITHUB_COMMENT_JSON_INVALID",r.reason)
        self.assertFalse(self.database.exists())


if __name__=="__main__":
    unittest.main()
