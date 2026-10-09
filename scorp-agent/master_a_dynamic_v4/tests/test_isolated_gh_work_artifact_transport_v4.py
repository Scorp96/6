from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4 import (
    _gh_get_comment, ingest_pinned_github_artifact_without_send,
)

ISSUE = 2432
AUTHOR = 130877686
COMMENT = 6075069271
PROJECT = "project-r2-canary"
ASSIGN = "assignment-synthetic-no-send"
TASK = "task-synthetic-artifact"
SHA = "a"*64


def rest_comment(
    *, ident=COMMENT, issue=ISSUE, author=AUTHOR,
    updated="2026-10-09T05:42:21Z", artifact_sha="b"*64,
):
    doc={
        "protocol":"scorp.github-work-artifact/1",
        "project_id":PROJECT,
        "assignment_id":ASSIGN,
        "task_id":TASK,
        "worker_slot":"worker-slot-1",
        "assignment_sha256":SHA,
        "artifact_sha256":artifact_sha,
        "state_version":0,
        "event":"ARTIFACT_STAGED_FOR_REVIEW",
    }
    return {
        "id":ident,
        "url":"https://api.github.com/repos/Scorp96/scorp-control-plane/issues/comments/"+str(ident),
        "html_url":"https://github.com/Scorp96/scorp-control-plane/issues/"+str(issue)+"#issuecomment-"+str(ident),
        "issue_url":"https://api.github.com/repos/Scorp96/scorp-control-plane/issues/"+str(issue),
        "user":{"id":author},
        "created_at":"2026-10-09T05:42:21Z",
        "updated_at":updated,
        "body":"SCORP_R2_WORK_ARTIFACT::"+json.dumps(doc,separators=(",",":")),
    }


class NativeGhWorkerIntakeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"experiments"
        self.root.mkdir()
        folder=self.root/"r2-work-artifact-review-gh-cli-fixture"
        folder.mkdir()
        self.db=folder/"work-artifact-review.sqlite3"

    def ingest(self, *, reader=None, ident=COMMENT, issue=ISSUE,
               author=AUTHOR, project=PROJECT, assignment=ASSIGN,
               sha=SHA, version=0):
        return ingest_pinned_github_artifact_without_send(
            database=self.db,allowed_experiments_root=self.root,
            comment_id=ident,expected_issue_number=issue,
            allowlisted_github_author_id=author,
            expected_project_id=project,expected_assignment_id=assignment,
            expected_task_id=TASK,expected_worker_slot="worker-slot-1",
            expected_assignment_sha256=sha,expected_state_version=version,
            reader=reader if reader is not None else (lambda _:rest_comment()),
        )

    def safe(self,r):
        self.assertFalse(r.browser_send_authorized)
        self.assertFalse(r.wake_master_authorized)
        self.assertFalse(r.wake_worker_authorized)
        self.assertFalse(r.chatgpt_turn_final_attested)
        self.assertFalse(r.production_write_authorized)
        self.assertEqual(0,r.local_model_calls)
        self.assertNotIn("SCORP_R2_WORK_ARTIFACT",repr(r))
        self.assertNotIn("https://github.com",repr(r))

    def test_real_rest_comment_fixture_stage_once(self):
        r=self.ingest()
        self.assertEqual("GITHUB_ARTIFACT_STAGED_REVIEW_ONLY",r.status)
        self.assertTrue(r.github_comment_read)
        self.assertTrue(r.review_row_committed)
        self.safe(r)
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(1,c.execute("SELECT COUNT(*) FROM r2_github_review_artifacts").fetchone()[0])

    def test_duplicate_comment_not_staged_again(self):
        self.ingest()
        r=self.ingest()
        self.assertEqual("BLOCKED",r.status)
        self.assertEqual("DURABLE_WORK_ARTIFACT_OR_ASSIGNMENT_ALREADY_SEEN",r.reason)
        self.assertFalse(r.review_row_committed)
        self.safe(r)

    def test_wrong_issue_number_never_committed(self):
        r=self.ingest(issue=2434)
        self.assertEqual("GITHUB_ISSUE_OR_COMMENT_LINK_UNVERIFIED",r.reason)
        self.assertFalse(self.db.exists())

    def test_wrong_author_number_never_committed(self):
        r=self.ingest(author=AUTHOR+1)
        self.assertEqual("GITHUB_AUTHOR_OR_COMMENT_ID_MISMATCH",r.reason)
        self.assertFalse(self.db.exists())

    def test_editing_comment_after_original_post_blocks(self):
        r=self.ingest(reader=lambda _:rest_comment(updated="2026-10-09T05:50:00Z"))
        self.assertEqual("GITHUB_COMMENT_EDIT_OR_TIME_UNVERIFIED",r.reason)
        self.safe(r)

    def test_forged_or_nonrest_api_link_blocks_before_sqlite(self):
        sample=rest_comment()
        sample["url"]="https://evil.invalid/comment"
        r=self.ingest(reader=lambda _:sample)
        self.assertEqual("GH_REST_COMMENT_LINK_MISMATCH",r.reason)
        self.assertFalse(self.db.exists())

    def test_bad_gh_api_comment_id_cannot_be_interpreted_as_completed(self):
        result=self.ingest(reader=lambda _:rest_comment(ident=COMMENT+1))
        self.assertEqual("GH_COMMENT_NOT_VERIFIED",result.reason)
        self.safe(result)

    def test_invalid_local_input_skips_network(self):
        calls=[]
        def read(*a):
            calls.append(a)
            return rest_comment()
        for ident in (0,-1,True,"6075069271",None):
            r=self.ingest(ident=ident,reader=read)
            self.assertEqual("PINNED_GITHUB_COMMENT_ID_INVALID",r.reason)
        self.assertEqual([],calls)
        self.assertFalse(self.db.exists())

    def test_missing_or_corrupt_remote_reply_fails_closed(self):
        for value in (None,{},[],{"id":COMMENT},"PRIVATE_CONTENT"):
            with self.subTest(kind=type(value).__name__):
                r=self.ingest(reader=lambda _:value)
                self.assertIn(
                    r.reason,
                    ("GH_COMMENT_NOT_VERIFIED", "GH_REST_COMMENT_LINK_MISMATCH"),
                )
                self.safe(r)
        self.assertFalse(self.db.exists())

    def test_reader_exception_does_not_leak_private_text(self):
        def fail(*args):
            raise ValueError("PRIVATE_COOKIE https://chatgpt.com/c/private")
        r=self.ingest(reader=fail)
        self.assertEqual("GH_READ_UNAVAILABLE",r.reason)
        self.assertNotIn("PRIVATE_COOKIE",repr(r))
        self.safe(r)

    def test_four_concurrent_reads_allow_only_one_write(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.ingest(),range(4)))
        self.assertEqual(1,sum(r.review_row_committed for r in results))
        self.assertTrue(all(not r.wake_master_authorized for r in results))

    def test_private_body_not_persisted_into_sqlite(self):
        self.ingest()
        data=self.db.read_bytes()
        for text in (
            b"SCORP_R2_WORK_ARTIFACT",PROJECT.encode(),ASSIGN.encode(),
            b"private-account",b"https://chatgpt.com",
        ):
            self.assertNotIn(text,data)

    def test_gh_executable_missing_fails_without_subprocess(self):
        with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.shutil.which",return_value=None):
            self.assertIsNone(_gh_get_comment(COMMENT))

    def test_gh_native_process_uses_exact_only_read_api_argv(self):
        observed={}
        class FakePopen:
            def __init__(self,argv,**kwargs):
                observed["argv"]=argv
                observed["opts"]=kwargs
                kwargs["stdout"].write(json.dumps(rest_comment()).encode("utf-8"))
                kwargs["stdout"].flush()
            def wait(self,timeout):
                observed["timeout"]=timeout
                return 0
            def kill(self):raise AssertionError("no kill on success")
        with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.shutil.which",return_value="gh.exe"):
            with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.subprocess.Popen",FakePopen):
                result=_gh_get_comment(COMMENT)
        self.assertEqual(COMMENT,result["id"])
        self.assertEqual(["gh.exe","api","--hostname","github.com",
                          "repos/Scorp96/scorp-control-plane/issues/comments/"+str(COMMENT)],
                         observed["argv"])
        self.assertEqual(subprocess.DEVNULL,observed["opts"]["stdin"])
        self.assertEqual(12,observed["timeout"])
        self.assertEqual("github.com",observed["opts"]["env"]["GH_HOST"])

    def test_gh_nonzero_exit_never_outputs_private_diagnostics(self):
        class FakePopen:
            def __init__(self,argv,**kwargs):
                kwargs["stdout"].write(b"PRIVATE_COOKIE")
                kwargs["stderr"].write(b"PRIVATE_COOKIE")
            def wait(self,timeout):return 1
            def kill(self):pass
        with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.shutil.which",return_value="gh.exe"):
            with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.subprocess.Popen",FakePopen):
                self.assertIsNone(_gh_get_comment(COMMENT))

    def test_gh_timeout_kills_without_reading_inherited_pipe(self):
        state={"kill":False}
        class FakePopen:
            def __init__(self,argv,**kwargs):pass
            def wait(self,timeout):
                if not state["kill"]:
                    raise subprocess.TimeoutExpired("gh",timeout)
                return 0
            def kill(self):state["kill"]=True
        with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.shutil.which",return_value="gh.exe"):
            with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.subprocess.Popen",FakePopen):
                self.assertIsNone(_gh_get_comment(COMMENT))
        self.assertTrue(state["kill"])

    def test_gh_response_over_limit_is_denied(self):
        class FakePopen:
            def __init__(self,argv,**kwargs):
                kwargs["stdout"].write(b"x"*16385)
            def wait(self,timeout):return 0
            def kill(self):pass
        with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.shutil.which",return_value="gh.exe"):
            with patch("master_a_dynamic_v4.isolated_gh_work_artifact_transport_v4.subprocess.Popen",FakePopen):
                self.assertIsNone(_gh_get_comment(COMMENT))


if __name__=="__main__":
    unittest.main()
