from __future__ import annotations
import base64
import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.isolated_gh_verified_result_latch_v4 import (
    stage_pinned_github_blob_comment_for_review,
)
from master_a_dynamic_v4.isolated_two_verified_blobs_review_v4 import (
    inspect_two_git_verified_worker_results,
)
from master_a_dynamic_v4.isolated_github_work_result_inbox_v4 import (
    stage_explicit_github_worker_artifact_for_review,
)

PROJECT="project-r2-canary"
COMMIT="a"*40
AUTHOR=130877686
ISSUE=2432
ASSIGN=("assignment-synthetic-no-send","assignment-synthetic-worker-two")
TASK=("task-synthetic-artifact","task-synthetic-worker-two")
SHA=("a"*64,"d"*64)
ID=(6075575072,6075577361)
PATH=("scorp-agent/r2-work-artifacts/synthetic-worker-one.json",
      "scorp-agent/r2-work-artifacts/synthetic-worker-two.json")

def fixtures(i):
    slot="worker-slot-"+str(i+1)
    artifact={
        "protocol":"scorp.r2.immutable-work-artifact/1",
        "project_id":PROJECT,"assignment_id":ASSIGN[i],
        "task_id":TASK[i],"worker_slot":slot,
        "state_version":0,"kind":"WORK_PRODUCT_FOR_REVIEW",
        "evidence_status":"ARTIFACT_PRESENT_NOT_GPT_FINAL",
    }
    data=json.dumps(artifact,sort_keys=True,separators=(",",":")).encode()
    digest=hashlib.sha256(data).hexdigest()
    blob={
        "type":"file","path":PATH[i],"encoding":"base64","size":len(data),
        "sha":hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest(),
        "content":base64.b64encode(data).decode("ascii"),
    }
    comment_document={
        "protocol":"scorp.github-work-artifact/1",
        "project_id":PROJECT,"assignment_id":ASSIGN[i],
        "task_id":TASK[i],"worker_slot":slot,
        "assignment_sha256":SHA[i],"artifact_sha256":digest,
        "state_version":0,"event":"ARTIFACT_STAGED_FOR_REVIEW",
    }
    issue_url="https://api.github.com/repos/Scorp96/scorp-control-plane/issues/"+str(ISSUE)
    comment={
        "id":ID[i],"user":{"id":AUTHOR},
        "url":"https://api.github.com/repos/Scorp96/scorp-control-plane/issues/comments/"+str(ID[i]),
        "html_url":"https://github.com/Scorp96/scorp-control-plane/issues/"+str(ISSUE)+"#issuecomment-"+str(ID[i]),
        "issue_url":issue_url,
        "created_at":"2026-10-09T05:30:00Z",
        "updated_at":"2026-10-09T05:30:00Z",
        "body":"SCORP_R2_WORK_ARTIFACT::"+json.dumps(comment_document),
    }
    return comment,blob,digest

class RealBlobBoundTwoWorkerReviewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/"experiments"
        self.root.mkdir()
        folder=self.root/"r2-work-artifact-review-immutable-unit"
        folder.mkdir()
        self.db=folder/"work-artifact-review.sqlite3"

    def run_stage(self,i,**overrides):
        comment,blob,digest=fixtures(i)
        inputs=dict(
            database=self.db,allowed_experiments_root=self.root,
            comment_id=ID[i],expected_issue_number=ISSUE,
            allowlisted_github_author_id=AUTHOR,
            expected_project_id=PROJECT,expected_assignment_id=ASSIGN[i],
            expected_task_id=TASK[i],expected_worker_slot="worker-slot-"+str(i+1),
            expected_assignment_sha256=SHA[i],expected_state_version=0,
            expected_artifact_sha256=digest,artifact_commit_sha=COMMIT,
            artifact_path=PATH[i],comment_reader=lambda _:comment,
            blob_reader=lambda *_:blob,
        )
        inputs.update(overrides)
        return stage_pinned_github_blob_comment_for_review(**inputs)

    def barrier(self,**overrides):
        scope=dict(
            database=self.db,allowed_experiments_root=self.root,
            expected_project_id=PROJECT,expected_state_version=0,
            worker_1_assignment_id=ASSIGN[0],
            worker_1_assignment_sha256=SHA[0],
            worker_1_issue_number=ISSUE,worker_1_author_id=AUTHOR,
            worker_2_assignment_id=ASSIGN[1],
            worker_2_assignment_sha256=SHA[1],
            worker_2_issue_number=ISSUE,worker_2_author_id=AUTHOR,
        )
        arguments=dict(
            barrier_kwargs=scope,first_commit_sha=COMMIT,
            first_artifact_path=PATH[0],second_commit_sha=COMMIT,
            second_artifact_path=PATH[1],
        )
        arguments.update(overrides)
        return inspect_two_git_verified_worker_results(**arguments)

    def safe(self,r):
        self.assertFalse(r.actual_worker_identity_verified)
        self.assertFalse(r.host_terminal_event_attested)
        self.assertFalse(r.browser_send_authorized)
        self.assertFalse(r.wake_master_authorized)
        self.assertFalse(r.wake_worker_authorized)
        self.assertFalse(r.local_execution_authorized)

    def test_first_stage_requires_matching_actual_blob(self):
        r=self.run_stage(0)
        self.assertEqual("BLOB_AND_COMMENT_STAGED_FOR_REVIEW",r.status)
        self.assertTrue(r.comment_and_blob_verified)
        self.assertTrue(r.evidence_recorded)
        self.safe(r)
        waiting=self.barrier()
        self.assertEqual("AWAITING_SCOPED_BASE_RESULTS",waiting.status)
        self.safe(waiting)

    def test_two_real_blob_proofs_join_only_for_human_review(self):
        self.assertTrue(self.run_stage(0).evidence_recorded)
        self.assertTrue(self.run_stage(1).evidence_recorded)
        r=self.barrier()
        self.assertEqual("BOTH_IMMUTABLE_BLOBS_FOR_HUMAN_REVIEW",r.status)
        self.assertTrue(r.both_git_blobs_verified_for_human_review)
        self.assertEqual(2,r.verified_artifact_rows)
        self.safe(r)

    def test_replayed_comment_cannot_stage_again(self):
        self.run_stage(0)
        r=self.run_stage(0)
        self.assertEqual("BLOCKED",r.status)
        self.assertEqual("DURABLE_WORK_ARTIFACT_OR_ASSIGNMENT_ALREADY_SEEN",r.reason)
        self.safe(r)

    def test_wrong_sha256_refuses_without_creating_sqlite(self):
        r=self.run_stage(0,expected_artifact_sha256="f"*64)
        self.assertEqual("COMMENT_ARTIFACT_DIGEST_BINDING_MISMATCH",r.reason)
        self.assertFalse(self.db.exists())

    def test_wrong_blob_bytes_not_staged(self):
        _,bad,_=fixtures(1)
        r=self.run_stage(0,blob_reader=lambda *_:bad)
        self.assertEqual("REAL_IMMUTABLE_BLOB_PROOF_REQUIRED",r.reason)
        self.assertFalse(self.db.exists())

    def test_swapped_path_and_comment_scope_fail_closed(self):
        r=self.run_stage(0,artifact_path=PATH[1])
        self.assertEqual("REAL_IMMUTABLE_BLOB_PROOF_REQUIRED",r.reason)
        self.assertFalse(self.db.exists())

    def test_fake_comment_author_refused_even_with_valid_blob(self):
        c,blob,_=fixtures(0)
        c["user"]["id"]+=1
        r=self.run_stage(0,comment_reader=lambda _:c)
        self.assertEqual("GITHUB_AUTHOR_OR_COMMENT_ID_MISMATCH",r.reason)
        self.assertFalse(self.db.exists())

    def test_malformed_comment_json_refused(self):
        c,_,_=fixtures(0)
        c["body"]="SCORP_R2_WORK_ARTIFACT::{bad}"
        r=self.run_stage(0,comment_reader=lambda _:c)
        self.assertEqual("COMMENT_RESULT_JSON_INVALID",r.reason)
        self.assertFalse(self.db.exists())

    def test_one_missing_attestation_never_counts_as_two_verified(self):
        self.run_stage(0)
        c,_,_=fixtures(1)
        simple=stage_explicit_github_worker_artifact_for_review(
            database=self.db,allowed_experiments_root=self.root,
            comment=c,verified_issue_number=ISSUE,
            allowlisted_github_author_id=AUTHOR,
            expected_project_id=PROJECT,expected_assignment_id=ASSIGN[1],
            expected_task_id=TASK[1],expected_worker_slot="worker-slot-2",
            expected_assignment_sha256=SHA[1],expected_state_version=0,
        )
        self.assertTrue(simple.staged_for_review)
        result=self.barrier()
        self.assertEqual("AWAITING_GIT_BLOB_EVIDENCE",result.status)
        self.assertEqual(1,result.verified_artifact_rows)
        self.safe(result)

    def test_mismatched_expected_commit_sha_fails_review_barrier(self):
        self.run_stage(0)
        self.run_stage(1)
        r=self.barrier(second_commit_sha="b"*40)
        self.assertEqual("BLOB_AND_COMMENT_SCOPE_MISMATCH",r.reason)
        self.safe(r)

    def test_wrong_expected_path_is_no_send_error(self):
        self.run_stage(0)
        self.run_stage(1)
        r=self.barrier(second_artifact_path="scorp-agent/r2-work-artifacts/other-worker.json")
        self.assertEqual("BLOB_AND_COMMENT_SCOPE_MISMATCH",r.reason)

    def test_reusing_one_artifact_path_for_two_slots_blocked(self):
        self.run_stage(0)
        self.run_stage(1)
        r=self.barrier(second_artifact_path=PATH[0])
        self.assertEqual("WORKER_ARTIFACT_PATH_REUSED",r.reason)

    def test_different_expected_generation_blocks(self):
        self.run_stage(0)
        self.run_stage(1)
        scope=self.barrier_scope(state=1)
        r=self.barrier(barrier_kwargs=scope)
        self.assertEqual("AWAITING_SCOPED_BASE_RESULTS",r.status)

    def barrier_scope(self,state=0):
        return dict(
            database=self.db,allowed_experiments_root=self.root,
            expected_project_id=PROJECT,expected_state_version=state,
            worker_1_assignment_id=ASSIGN[0],
            worker_1_assignment_sha256=SHA[0],
            worker_1_issue_number=ISSUE,worker_1_author_id=AUTHOR,
            worker_2_assignment_id=ASSIGN[1],
            worker_2_assignment_sha256=SHA[1],
            worker_2_issue_number=ISSUE,worker_2_author_id=AUTHOR,
        )

    def test_partial_schema_change_fails_without_migration(self):
        self.run_stage(0)
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            c.execute("DROP TABLE r2_immutable_artifact_attestations")
            c.commit()
        r=self.barrier()
        self.assertEqual("AWAITING_SCOPED_BASE_RESULTS",r.status)
        self.safe(r)

    def test_error_string_never_contains_comment_content_or_ids(self):
        self.run_stage(0)
        self.run_stage(1)
        r=self.barrier()
        for private in ("https://github.com",PROJECT,ASSIGN[0],TASK[1],str(ID[0])):
            self.assertNotIn(private,repr(r))

    def test_readonly_barrier_makes_no_modification(self):
        self.run_stage(0)
        self.run_stage(1)
        before=self.db.stat().st_mtime_ns
        first=self.barrier()
        second=self.barrier()
        self.assertEqual(first,second)
        self.assertEqual(before,self.db.stat().st_mtime_ns)

if __name__=="__main__":
    unittest.main()
