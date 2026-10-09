from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.isolated_github_work_result_inbox_v4 import (
    stage_explicit_github_worker_artifact_for_review,
)
from master_a_dynamic_v4.isolated_two_worker_artifact_review_v4 import (
    inspect_two_worker_review_only,
)

PROJECT = "project-synthetic-two"
A1 = "assignment-synthetic-one"
A2 = "assignment-synthetic-two"
S1 = "a"*64
S2 = "b"*64
T1 = "task-synthetic-one"
T2 = "task-synthetic-two"
AUTHOR = 930001
ISSUE1 = 51001
ISSUE2 = 51002


def provider(assignment,slot,sha,task,issue,ident,state=7,project=PROJECT):
    obj={
        "protocol":"scorp.github-work-artifact/1",
        "project_id":project,
        "assignment_id":assignment,
        "task_id":task,
        "worker_slot":slot,
        "assignment_sha256":sha,
        "artifact_sha256":"c"*64 if slot=="worker-slot-1" else "d"*64,
        "state_version":state,
        "event":"ARTIFACT_STAGED_FOR_REVIEW",
    }
    return {
        "id":ident,"user":{"id":AUTHOR},
        "url":("https://github.com/Scorp96/scorp-control-plane/issues/"
               +str(issue)+"#issuecomment-"+str(ident)),
        "created_at":"2026-10-09T05:00:00Z",
        "updated_at":"2026-10-09T05:00:00Z",
        "body":"SCORP_R2_WORK_ARTIFACT::"+json.dumps(obj,separators=(",",":")),
    }


class TwoWorkerArtifactReviewBarrierTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"experiments"
        self.root.mkdir()
        folder=self.root/"r2-work-artifact-review-two-workers-test"
        folder.mkdir()
        self.db=folder/"work-artifact-review.sqlite3"

    def add(self,slot,**overrides):
        first=slot==1
        assignment=overrides.get("assignment",A1 if first else A2)
        sha=overrides.get("sha",S1 if first else S2)
        issue=overrides.get("issue",ISSUE1 if first else ISSUE2)
        task=T1 if first else T2
        state=overrides.get("state",7)
        project=overrides.get("project",PROJECT)
        author=overrides.get("author",AUTHOR)
        comment=provider(
            assignment,"worker-slot-1" if first else "worker-slot-2",
            sha,task,issue,780001 if first else 780002,state=state,project=project,
        )
        return stage_explicit_github_worker_artifact_for_review(
            database=self.db,allowed_experiments_root=self.root,
            comment=comment,verified_issue_number=issue,
            allowlisted_github_author_id=author,
            expected_project_id=project,expected_assignment_id=assignment,
            expected_task_id=task,
            expected_worker_slot="worker-slot-1" if first else "worker-slot-2",
            expected_assignment_sha256=sha,expected_state_version=state,
        )

    def inspect(self,**overrides):
        data={
            "database":self.db,"allowed_experiments_root":self.root,
            "expected_project_id":PROJECT,"expected_state_version":7,
            "worker_1_assignment_id":A1,"worker_1_assignment_sha256":S1,
            "worker_1_issue_number":ISSUE1,"worker_1_author_id":AUTHOR,
            "worker_2_assignment_id":A2,"worker_2_assignment_sha256":S2,
            "worker_2_issue_number":ISSUE2,"worker_2_author_id":AUTHOR,
        }
        data.update(overrides)
        return inspect_two_worker_review_only(**data)

    def safe(self,result):
        self.assertFalse(result.real_worker_identity_attested)
        self.assertFalse(result.host_terminal_event_attested)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.wake_master_authorized)
        self.assertFalse(result.wake_worker_authorized)
        self.assertFalse(result.local_execution_authorized)

    def test_missing_database_fails_closed(self):
        result=self.inspect()
        self.assertEqual("ISOLATED_WORK_REVIEW_LEDGER_REQUIRED",result.reason)
        self.safe(result)

    def test_first_worker_review_never_wakes_master(self):
        self.assertTrue(self.add(1).staged_for_review)
        result=self.inspect()
        self.assertEqual("AWAITING_SECOND_WORKER",result.status)
        self.assertEqual(1,result.review_artifacts_present)
        self.safe(result)

    def test_two_independent_results_join_for_human_review_only(self):
        self.assertTrue(self.add(1).staged_for_review)
        self.assertTrue(self.add(2).staged_for_review)
        result=self.inspect()
        self.assertEqual("BOTH_ARTIFACTS_FOR_REVIEW",result.status)
        self.assertEqual(2,result.review_artifacts_present)
        self.assertTrue(result.both_artifacts_staged_for_human_review)
        self.safe(result)
        for s in (PROJECT,A1,A2,"https://chatgpt.com","PRIVATE"):
            self.assertNotIn(s,repr(result))

    def test_repeat_inspect_has_no_side_effects(self):
        self.add(1)
        self.add(2)
        before=self.db.stat().st_mtime_ns
        one=self.inspect()
        two=self.inspect()
        after=self.db.stat().st_mtime_ns
        self.assertEqual(one,two)
        self.assertEqual(before,after)
        self.safe(two)

    def test_mixed_state_version_denied(self):
        self.add(1)
        self.add(2,state=8)
        result=self.inspect()
        self.assertEqual("REVIEW_WORKER_SCOPE_OR_EVIDENCE_MISMATCH",result.reason)
        self.safe(result)

    def test_different_project_not_accepted(self):
        self.add(1)
        self.add(2,project="project-another-valid")
        result=self.inspect()
        self.assertEqual("AWAITING_SECOND_WORKER",result.status)
        self.safe(result)

    def test_wrong_worker_2_assignment_digest_not_accepted(self):
        self.add(1)
        self.add(2,assignment="assignment-foreign-valid")
        result=self.inspect()
        self.assertEqual("AWAITING_SECOND_WORKER",result.status)
        self.safe(result)

    def test_wrong_github_issue_scope_denied(self):
        self.add(1)
        self.add(2)
        result=self.inspect(worker_2_issue_number=ISSUE2+1)
        self.assertEqual("REVIEW_WORKER_SCOPE_OR_EVIDENCE_MISMATCH",result.reason)
        self.safe(result)

    def test_wrong_author_scope_denied(self):
        self.add(1)
        self.add(2)
        result=self.inspect(worker_1_author_id=AUTHOR+1)
        self.assertEqual("REVIEW_WORKER_SCOPE_OR_EVIDENCE_MISMATCH",result.reason)

    def test_different_expected_assignment_sha_denied(self):
        self.add(1)
        self.add(2)
        result=self.inspect(worker_2_assignment_sha256="f"*64)
        self.assertEqual("REVIEW_WORKER_SCOPE_OR_EVIDENCE_MISMATCH",result.reason)

    def test_generation_boolean_invalid(self):
        self.add(1)
        self.add(2)
        result=self.inspect(expected_state_version=True)
        self.assertEqual("EXPECTED_TWO_WORKER_SCOPE_INVALID",result.reason)

    def test_identical_assignments_not_two_independent_workers(self):
        self.add(1)
        self.add(2)
        result=self.inspect(worker_2_assignment_id=A1)
        self.assertEqual("EXPECTED_TWO_WORKER_SCOPE_INVALID",result.reason)

    def test_cannot_rebind_to_original_master_identity(self):
        self.add(1)
        self.add(2)
        result=self.inspect(worker_2_assignment_id="master")
        self.assertEqual("EXPECTED_TWO_WORKER_SCOPE_INVALID",result.reason)

    def test_concurrent_readonly_barrier_is_stable(self):
        self.add(1)
        self.add(2)
        with ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(lambda _:self.inspect(),range(5)))
        self.assertTrue(all(r.status=="BOTH_ARTIFACTS_FOR_REVIEW" for r in results))
        self.assertTrue(all(not r.browser_send_authorized for r in results))

    def test_corrupted_database_stays_blocked(self):
        self.db.write_bytes(b"corrupted db")
        result=self.inspect()
        self.assertEqual("WORK_REVIEW_LEDGER_UNREADABLE",result.reason)
        self.safe(result)

    def test_unknown_result_hash_denied_at_insert(self):
        self.add(1)
        result=self.add(2,sha="z"*64)
        self.assertFalse(result.staged_for_review)
        final=self.inspect()
        self.assertEqual("AWAITING_SECOND_WORKER",final.status)
        self.safe(final)


if __name__=="__main__":
    unittest.main()
