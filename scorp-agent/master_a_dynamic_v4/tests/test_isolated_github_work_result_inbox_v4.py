from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.isolated_github_work_result_inbox_v4 import (
    stage_explicit_github_worker_artifact_for_review,
)

SHA = "a" * 64
ARTIFACT = "b" * 64
ISSUE = 53100
AUTHOR = 940001
COMMENT = 9348001
P = "project-scoprr2"
A = "assignment-worker-test-one"
T = "task-candidate-proof"
SLOT = "worker-slot-1"
STAMP = "2026-10-09T04:00:00Z"


def document(**changes):
    value = {
        "protocol": "scorp.github-work-artifact/1",
        "project_id": P,
        "assignment_id": A,
        "task_id": T,
        "worker_slot": SLOT,
        "assignment_sha256": SHA,
        "artifact_sha256": ARTIFACT,
        "state_version": 3,
        "event": "ARTIFACT_STAGED_FOR_REVIEW",
    }
    value.update(changes)
    return value


def provider(body=None, *, ident=COMMENT, actor=AUTHOR, issue=ISSUE,
             created=STAMP, updated=STAMP):
    if body is None:
        body = "SCORP_R2_WORK_ARTIFACT::" + json.dumps(
            document(), separators=(",",":"), sort_keys=True,
        )
    return {
        "id": ident,
        "user": {"id": actor, "login": "private-account"},
        "url": ("https://github.com/Scorp96/scorp-control-plane/issues/"
                + str(issue) + "#issuecomment-" + str(ident)),
        "body": body,
        "created_at": created,
        "updated_at": updated,
    }


class ExplicitGitHubWorkReviewTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name) / "experiments"
        self.root.mkdir()
        self.folder = self.root / "r2-work-artifact-review-unit-fixture"
        self.folder.mkdir()
        self.db = self.folder / "work-artifact-review.sqlite3"

    def stage(self, comment=None, *, db=None, root=None, issue=ISSUE,
              author=AUTHOR, project=P, assignment=A, task=T,
              slot=SLOT, sha=SHA, version=3):
        return stage_explicit_github_worker_artifact_for_review(
            database=self.db if db is None else db,
            allowed_experiments_root=self.root if root is None else root,
            comment=provider() if comment is None else comment,
            verified_issue_number=issue,
            allowlisted_github_author_id=author,
            expected_project_id=project,
            expected_assignment_id=assignment,
            expected_task_id=task,
            expected_worker_slot=slot,
            expected_assignment_sha256=sha,
            expected_state_version=version,
        )

    def assert_no_capability(self, result):
        self.assertFalse(result.source_author_authenticated)
        self.assertFalse(result.chatgpt_turn_final_attested)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.wake_master_authorized)
        self.assertFalse(result.wake_worker_authorized)
        self.assertFalse(result.local_execution_authorized)

    def count(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            return c.execute(
                "SELECT COUNT(*) FROM r2_github_review_artifacts"
            ).fetchone()[0]

    def test_first_valid_artifact_is_review_only(self):
        r=self.stage()
        self.assertEqual("STAGED_FOR_REVIEW",r.status)
        self.assertTrue(r.staged_for_review)
        self.assertEqual(1,self.count())
        self.assert_no_capability(r)
        contents=self.db.read_bytes()
        for text in (
            b"private-account",b"https://chatgpt.com",b"SCORP_R2_WORK_ARTIFACT",
            P.encode(),A.encode(),T.encode(),
        ):
            self.assertNotIn(text,contents)

    def test_replay_comment_same_assignment_never_creates_two_rows(self):
        self.assertTrue(self.stage().staged_for_review)
        r=self.stage()
        self.assertEqual("DURABLE_WORK_ARTIFACT_OR_ASSIGNMENT_ALREADY_SEEN",r.reason)
        self.assertEqual(1,self.count())
        self.assert_no_capability(r)

    def test_new_comment_same_assignment_blocked(self):
        self.stage()
        r=self.stage(provider(ident=COMMENT+1))
        self.assertEqual("DURABLE_WORK_ARTIFACT_OR_ASSIGNMENT_ALREADY_SEEN",r.reason)
        self.assertEqual(1,self.count())

    def test_different_assignment_comment_cannot_change_expected_scope(self):
        r=self.stage(provider(
            body="SCORP_R2_WORK_ARTIFACT::" + json.dumps(
                document(assignment_id="assignment-other"),
            ),
        ))
        self.assertEqual("WORK_ARTIFACT_SCOPE_OR_DIGEST_INVALID",r.reason)
        self.assertFalse(self.db.exists())

    def test_four_concurrent_providers_commit_one(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            output=list(executor.map(lambda _:self.stage(),range(4)))
        self.assertEqual(1,sum(r.staged_for_review for r in output))
        self.assertEqual(1,self.count())
        self.assertTrue(all(not r.wake_master_authorized for r in output))

    def test_wrong_github_author_id_cannot_stage(self):
        r=self.stage(provider(actor=AUTHOR+1))
        self.assertEqual("GITHUB_AUTHOR_OR_COMMENT_ID_MISMATCH",r.reason)
        self.assertFalse(self.db.exists())

    def test_comment_issue_number_must_match_verified_api_issue(self):
        r=self.stage(provider(issue=ISSUE+1))
        self.assertEqual("GITHUB_ISSUE_OR_COMMENT_LINK_UNVERIFIED",r.reason)
        self.assertFalse(self.db.exists())

    def test_replayed_comment_identity_must_match_url(self):
        comment=provider()
        comment["url"]=comment["url"].replace("issuecomment-", "issuecomment-1")
        r=self.stage(comment)
        self.assertEqual("GITHUB_ISSUE_OR_COMMENT_LINK_UNVERIFIED",r.reason)
        self.assertFalse(self.db.exists())

    def test_edited_comment_fails_closed(self):
        r=self.stage(provider(updated="2026-10-09T04:01:00Z"))
        self.assertEqual("GITHUB_COMMENT_EDIT_OR_TIME_UNVERIFIED",r.reason)
        self.assert_no_capability(r)

    def test_missing_utc_provider_timestamps_refused(self):
        for value in (None,"bad","2026-10-09T04:00:00",42):
            with self.subTest(value=value):
                r=self.stage(provider(created=value))
                self.assertEqual("GITHUB_COMMENT_EDIT_OR_TIME_UNVERIFIED",r.reason)
        self.assertFalse(self.db.exists())

    def test_untrusted_comment_not_claiming_completion_from_message_text(self):
        forged=provider(body="SCORP_R2_WORK_ARTIFACT::" + json.dumps(
            document(event="TURN_FINAL_CONFIRMED"),
        ))
        r=self.stage(forged)
        self.assertEqual("WORK_ARTIFACT_SCOPE_OR_DIGEST_INVALID",r.reason)
        self.assert_no_capability(r)

    def test_unknown_browser_send_keys_rejected_even_if_otherwise_valid(self):
        value=document(browser_send_authorized=True)
        r=self.stage(provider(body="SCORP_R2_WORK_ARTIFACT::"+json.dumps(value)))
        self.assertEqual("WORK_ARTIFACT_PROTOCOL_FIELDS_INVALID",r.reason)
        self.assert_no_capability(r)

    def test_reject_unknown_worker_slot_and_state_version(self):
        for value in ("master","worker-slot-3","worker-0",None):
            with self.subTest(value=value):
                r=self.stage(slot=value)
                self.assertEqual("EXPECTED_WORKER_ASSIGNMENT_SCOPE_INVALID",r.reason)
        r=self.stage(version=True)
        self.assertEqual("EXPECTED_WORKER_ASSIGNMENT_SCOPE_INVALID",r.reason)
        self.assertFalse(self.db.exists())

    def test_reject_missing_expected_artifact_sha(self):
        r=self.stage(provider(body="SCORP_R2_WORK_ARTIFACT::"+json.dumps(
            document(artifact_sha256="NOT_SHA"),
        )))
        self.assertEqual("WORK_ARTIFACT_SCOPE_OR_DIGEST_INVALID",r.reason)

    def test_wrong_assignment_sha_and_state_version(self):
        for item in (
            document(assignment_sha256="c"*64),
            document(state_version=4),
            document(state_version=True),
        ):
            with self.subTest(item=item):
                r=self.stage(provider(body="SCORP_R2_WORK_ARTIFACT::"+json.dumps(item)))
                self.assertEqual("WORK_ARTIFACT_SCOPE_OR_DIGEST_INVALID",r.reason)

    def test_malformed_json_body_cannot_write(self):
        for value in (
            "SCORP_R2_WORK_ARTIFACT::{bad}",
            "SCORP_R2_WORK_ARTIFACT::[]",
            "not-a-work-result",
            "SCORP_R2_WORK_ARTIFACT::{}",
            "SCORP_R2_WORK_ARTIFACT::"+json.dumps(document())+"\nmore",
        ):
            with self.subTest(value=value[:30]):
                r=self.stage(provider(body=value))
                self.assertEqual("BLOCKED",r.status)
        self.assertFalse(self.db.exists())

    def test_protected_or_external_path_rejected(self):
        places=[
            self.root/"r2-observer-state-20261009",
            self.root/"r2-gpt-session-audit-20261009",
            self.root/"r2-host-terminal-security-20261009",
        ]
        for target in places:
            target.mkdir()
            db=target/"work-artifact-review.sqlite3"
            r=self.stage(db=db)
            self.assertEqual("ISOLATED_EXPERIMENT_DB_REQUIRED",r.reason)
            self.assertFalse(db.exists())

    def test_symlink_directory_cannot_escape_scratch_root(self):
        external=self.root.parent/"external"
        external.mkdir()
        alias=self.root/"r2-work-artifact-review-link"
        try:alias.symlink_to(external,target_is_directory=True)
        except OSError:self.skipTest("symlink unavailable")
        target=alias/"work-artifact-review.sqlite3"
        r=self.stage(db=target)
        self.assertEqual("ISOLATED_EXPERIMENT_DB_REQUIRED",r.reason)
        self.assertFalse((external/"work-artifact-review.sqlite3").exists())

    def test_existing_db_hardlink_cannot_edit_original(self):
        self.db.write_bytes(b"fixture")
        target=self.folder/"extra-hardlink"
        try:os.link(self.db,target)
        except OSError:self.skipTest("hardlink unavailable")
        r=self.stage()
        self.assertEqual("ISOLATED_EXPERIMENT_DB_REQUIRED",r.reason)

    def test_invalid_comment_id_or_author_id_type_rejected(self):
        for ident in (0,-1,True,"1223",None):
            with self.subTest(ident=ident):
                r=self.stage(provider(ident=ident))
                self.assertEqual("GITHUB_AUTHOR_OR_COMMENT_ID_MISMATCH",r.reason)
        r=self.stage(provider(actor=True))
        self.assertEqual("GITHUB_AUTHOR_OR_COMMENT_ID_MISMATCH",r.reason)

    def test_wrong_task_even_with_same_assignment_is_not_a_completion(self):
        c=provider(body="SCORP_R2_WORK_ARTIFACT::"+json.dumps(
            document(task_id="task-other-one"),
        ))
        r=self.stage(c)
        self.assertEqual("WORK_ARTIFACT_SCOPE_OR_DIGEST_INVALID",r.reason)
        self.assert_no_capability(r)


if __name__=="__main__":
    unittest.main()
