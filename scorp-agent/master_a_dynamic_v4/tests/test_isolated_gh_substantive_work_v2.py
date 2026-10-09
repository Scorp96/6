"""V2 meaningful work-product presence: never GPT session or terminal proof."""
import base64
import contextlib
import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.isolated_gh_immutable_artifact_v4 import (
    verify_immutable_github_artifact_for_review,
)
from master_a_dynamic_v4.isolated_gh_verified_result_latch_v4 import (
    stage_pinned_github_blob_comment_for_review,
)
from master_a_dynamic_v4.isolated_two_verified_blobs_review_v4 import (
    inspect_two_git_verified_worker_results,
)

PROJECT="project-r2-realreview"
ISSUE=2450
AUTHOR=130877686
COMMIT="a"*40
ASG=("assignment-real-one","assignment-real-two")
TASK=("task-review-one","task-review-two")
ASSIGN_SHA=("b"*64,"c"*64)
PATH=("scorp-agent/r2-work-artifacts/worker-content-one.json",
      "scorp-agent/r2-work-artifacts/worker-content-two.json")
CID=(6079999001,6079999002)


def make_artifact(idx,*,v2=True,deliverable=None):
    work={
        "title":"SCORP recovery safety analysis for independent review",
        "deliverable_markdown":deliverable if deliverable is not None else (
            "The review separates persistent task ownership from browser submission. "
            "A durable intent must be committed before any remote action; on "
            "an ambiguous delivery the safe state stays BLOCKED. The existing "
            "R1 Master is not an acceptable test target. Worker result file "
            "integrity verifies Git bytes but does not prove which ChatGPT "
            "session wrote the file or that its answer was finished. Any "
            "future dispatch gate must check independently recorded session "
            "authority, one-time intent and host provenance before send."
        ),
        "review_checks":[
            "Verify exact immutable commit and file SHA256 before accepting this artifact.",
            "Keep browser_send_authorized false in every code path through this result.",
        ],
        "source_refs":["https://github.com/Scorp96/6/pull/1"],
    }
    doc={
        "protocol":"scorp.r2.immutable-work-artifact/2" if v2 else "scorp.r2.immutable-work-artifact/1",
        "project_id":PROJECT,
        "assignment_id":ASG[idx],
        "task_id":TASK[idx],
        "worker_slot":"worker-slot-"+str(idx+1),
        "state_version":0,
        "kind":"WORK_PRODUCT_FOR_REVIEW",
        "evidence_status":"ARTIFACT_PRESENT_NOT_GPT_FINAL",
    }
    if v2:
        doc["work_product"]=work
    raw=json.dumps(doc,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    sha=hashlib.sha256(raw).hexdigest()
    blob={
        "path":PATH[idx],"type":"file","encoding":"base64","size":len(raw),
        "sha":hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest(),
        "content":base64.b64encode(raw).decode(),
    }
    payload={
        "protocol":"scorp.github-work-artifact/1",
        "project_id":PROJECT,"assignment_id":ASG[idx],"task_id":TASK[idx],
        "worker_slot":"worker-slot-"+str(idx+1),
        "assignment_sha256":ASSIGN_SHA[idx],
        "artifact_sha256":sha,"state_version":0,
        "event":"ARTIFACT_STAGED_FOR_REVIEW",
    }
    url="https://api.github.com/repos/Scorp96/scorp-control-plane/issues/"
    comment={
        "id":CID[idx],"user":{"id":AUTHOR},
        "url":url+"comments/"+str(CID[idx]),
        "issue_url":url+str(ISSUE),
        "html_url":"https://github.com/Scorp96/scorp-control-plane/issues/"+str(ISSUE)+"#issuecomment-"+str(CID[idx]),
        "created_at":"2026-10-09T07:00:00Z",
        "updated_at":"2026-10-09T07:00:00Z",
        "body":"SCORP_R2_WORK_ARTIFACT::"+json.dumps(payload,separators=(",",":")),
    }
    return blob,comment,sha


class SubstantiveWorkV2Tests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=pathlib.Path(t.name)/"experiments"
        self.root.mkdir()
        w=self.root/"r2-work-artifact-review-v2"
        w.mkdir()
        self.database=w/"work-artifact-review.sqlite3"

    def verify(self,i=0,*,v2=True,deliverable=None,require=True):
        blob,_,digest=make_artifact(i,v2=v2,deliverable=deliverable)
        return verify_immutable_github_artifact_for_review(
            path=PATH[i],commit_sha=COMMIT,expected_artifact_sha256=digest,
            expected_project_id=PROJECT,expected_assignment_id=ASG[i],
            expected_task_id=TASK[i],expected_worker_slot="worker-slot-"+str(i+1),
            expected_state_version=0,reader=lambda *_:blob,
            require_substantive_work_product=require,
        )

    def stage(self,i,*,v2=True,require=True):
        blob,comment,digest=make_artifact(i,v2=v2)
        return stage_pinned_github_blob_comment_for_review(
            database=self.database,allowed_experiments_root=self.root,
            comment_id=CID[i],expected_issue_number=ISSUE,
            allowlisted_github_author_id=AUTHOR,
            expected_project_id=PROJECT,expected_assignment_id=ASG[i],
            expected_task_id=TASK[i],expected_worker_slot="worker-slot-"+str(i+1),
            expected_assignment_sha256=ASSIGN_SHA[i],expected_state_version=0,
            expected_artifact_sha256=digest,artifact_commit_sha=COMMIT,
            artifact_path=PATH[i],comment_reader=lambda *_:comment,
            blob_reader=lambda *_:blob,require_substantive_work_product=require,
        )

    def barrier(self,*,require=True):
        return inspect_two_git_verified_worker_results(
            barrier_kwargs={
                "database":self.database,"allowed_experiments_root":self.root,
                "expected_project_id":PROJECT,"expected_state_version":0,
                "worker_1_assignment_id":ASG[0],"worker_1_assignment_sha256":ASSIGN_SHA[0],
                "worker_1_issue_number":ISSUE,"worker_1_author_id":AUTHOR,
                "worker_2_assignment_id":ASG[1],"worker_2_assignment_sha256":ASSIGN_SHA[1],
                "worker_2_issue_number":ISSUE,"worker_2_author_id":AUTHOR,
            },first_commit_sha=COMMIT,first_artifact_path=PATH[0],
            second_commit_sha=COMMIT,second_artifact_path=PATH[1],
            require_substantive_work_product=require,
        )

    def test_v2_verified_as_human_review_only(self):
        r=self.verify()
        self.assertTrue(r.substantive_work_product_present)
        self.assertEqual(64,len(r.work_product_sha256))
        self.assertFalse(r.browser_send_authorized)
        self.assertFalse(r.actual_worker_identity_verified)
        self.assertFalse(r.host_terminal_event_attested)

    def test_metadata_only_v1_must_not_count_as_real_deliverable(self):
        r=self.verify(v2=False)
        self.assertEqual("SUBSTANTIVE_WORK_PRODUCT_V2_REQUIRED",r.reason)
        self.assertFalse(r.substantive_work_product_present)

    def test_legacy_v1_still_accepted_in_legacy_mode_without_auth(self):
        r=self.verify(v2=False,require=False)
        self.assertTrue(r.content_digest_verified)
        self.assertFalse(r.substantive_work_product_present)
        self.assertFalse(r.actual_worker_identity_verified)

    def test_whitespace_and_stub_work_are_rejected_even_if_bytes_match(self):
        for body in (" " * 400,"This is just a header", ""):
            with self.subTest(body=body[:10]):
                r=self.verify(deliverable=body)
                self.assertEqual("SUBSTANTIVE_WORK_PRODUCT_INVALID",r.reason)
                self.assertFalse(r.content_digest_verified)

    def test_first_real_content_is_not_two_workers(self):
        r=self.stage(0)
        self.assertTrue(r.evidence_recorded)
        b=self.barrier()
        self.assertEqual("AWAITING_SCOPED_BASE_RESULTS",b.status)
        self.assertFalse(b.browser_send_authorized)

    def test_two_v2_files_have_unreviewed_text_receipts_only(self):
        self.assertTrue(self.stage(0).evidence_recorded)
        self.assertTrue(self.stage(1).evidence_recorded)
        b=self.barrier()
        self.assertEqual("BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW",b.status)
        self.assertFalse(b.actual_worker_identity_verified)
        self.assertFalse(b.host_terminal_event_attested)
        self.assertFalse(b.browser_send_authorized)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(2,db.execute("SELECT COUNT(*) FROM r2_substantive_work_receipts").fetchone()[0])
            self.assertEqual("ok",db.execute("PRAGMA quick_check").fetchone()[0])

    def test_one_v1_blob_cannot_pass_v2_barrier(self):
        self.stage(0)
        self.stage(1,v2=False,require=False)
        self.assertEqual("AWAITING_SUBSTANTIVE_WORK_PRODUCT",self.barrier().status)

    def test_missing_text_attestation_blocks_after_crash(self):
        self.stage(0)
        self.stage(1)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM r2_substantive_work_receipts WHERE comment_id=?",(CID[1],))
            db.commit()
        r=self.barrier()
        self.assertEqual("AWAITING_SUBSTANTIVE_WORK_PRODUCT",r.status)

    def test_tampered_substantive_status_cannot_authorize(self):
        self.stage(0);self.stage(1)
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE r2_substantive_work_receipts SET deliverable_sha256=? WHERE comment_id=?",("bad",CID[0]))
            db.commit()
        self.assertEqual("BLOCKED",self.barrier().status)

    def test_no_send_or_master_wake_functions(self):
        import master_a_dynamic_v4.isolated_gh_verified_result_latch_v4 as latch
        for name in ("submit_browser","wake_master","resume_original_r1","rotate_master","send"):
            self.assertFalse(hasattr(latch,name))


if __name__=="__main__":
    unittest.main()
