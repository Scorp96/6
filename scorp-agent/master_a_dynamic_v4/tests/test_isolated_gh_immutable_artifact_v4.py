from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import unittest
from unittest.mock import patch

from master_a_dynamic_v4.isolated_gh_immutable_artifact_v4 import (
    _native_gh_pinned_file, verify_immutable_github_artifact_for_review,
)

COMMIT = "a"*40
PATH = "scorp-agent/r2-work-artifacts/synthetic-worker-one.json"
PROJECT = "project-r2-canary"
ASSIGNMENT = "assignment-synthetic-no-send"
TASK = "task-synthetic-artifact"

def fixture(**changes):
    doc = {
        "protocol": "scorp.r2.immutable-work-artifact/1",
        "project_id": PROJECT,
        "assignment_id": ASSIGNMENT,
        "task_id": TASK,
        "worker_slot": "worker-slot-1",
        "state_version": 0,
        "kind": "WORK_PRODUCT_FOR_REVIEW",
        "evidence_status": "ARTIFACT_PRESENT_NOT_GPT_FINAL",
    }
    doc.update(changes)
    raw = json.dumps(doc,sort_keys=True,separators=(",",":")).encode()
    payload = {
        "path":PATH,
        "type":"file",
        "encoding":"base64",
        "size":len(raw),
        "sha":hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\x00"+raw).hexdigest(),
        "content":base64.b64encode(raw).decode("ascii"),
    }
    return payload, hashlib.sha256(raw).hexdigest()

class ImmutableGithubArtifactTests(unittest.TestCase):
    def verify(self, payload=None, **kwargs):
        good, digest = fixture()
        args=dict(
            path=PATH,commit_sha=COMMIT,expected_artifact_sha256=digest,
            expected_project_id=PROJECT,expected_assignment_id=ASSIGNMENT,
            expected_task_id=TASK,expected_worker_slot="worker-slot-1",
            expected_state_version=0,reader=lambda *_: payload if payload is not None else good,
        )
        args.update(kwargs)
        return verify_immutable_github_artifact_for_review(**args)

    def never_authorize(self,r):
        self.assertFalse(r.actual_worker_identity_verified)
        self.assertFalse(r.host_terminal_event_attested)
        self.assertFalse(r.browser_send_authorized)
        self.assertFalse(r.wake_master_authorized)
        self.assertFalse(r.wake_worker_authorized)
        self.assertFalse(r.local_execution_authorized)

    def test_exact_sha256_blob_and_scope_returns_review_only(self):
        result=self.verify()
        self.assertEqual("IMMUTABLE_ARTIFACT_VERIFIED_FOR_REVIEW",result.status)
        self.assertTrue(result.content_digest_verified)
        self.assertTrue(result.git_blob_identity_verified)
        self.assertTrue(result.scope_verified)
        self.never_authorize(result)

    def test_content_digest_mismatch_is_blocked(self):
        r=self.verify(expected_artifact_sha256="c"*64)
        self.assertEqual("PINNED_ARTIFACT_SHA256_MISMATCH",r.reason)
        self.never_authorize(r)

    def test_blob_id_mismatch_rejects_even_with_matching_sha256(self):
        payload,_=fixture()
        payload["sha"]="f"*40
        r=self.verify(payload)
        self.assertEqual("PINNED_ARTIFACT_BLOB_ID_MISMATCH",r.reason)

    def test_branch_name_not_accepted_as_commit(self):
        for ref in ("main","refs/heads/main","a"*39,"a"*41,True,None):
            with self.subTest(ref=ref):
                self.assertEqual("PINNED_ARTIFACT_CONTRACT_INVALID",self.verify(commit_sha=ref).reason)

    def test_path_traversal_other_repo_and_extensions_refused(self):
        for path in (
            "../state-v3/active/private",PATH+"?ref=main",
            "scorp-agent/r2-work-artifacts/../../secret",
            "scorp-agent/r2-work-artifacts/a.json",
            "scorp-agent/r2-work-artifacts/worker-one.py",
            "other/private.json",
            "/etc/passwd",
        ):
            with self.subTest(path=path):
                self.assertEqual("PINNED_ARTIFACT_CONTRACT_INVALID",self.verify(path=path).reason)

    def test_directory_or_symlink_metadata_is_not_a_file(self):
        for typ in ("symlink","dir",None):
            payload,_=fixture()
            payload["type"]=typ
            self.assertEqual("PINNED_ARTIFACT_GITHUB_METADATA_INVALID",self.verify(payload).reason)

    def test_server_path_mismatch_blocks(self):
        payload,_=fixture()
        payload["path"]="scorp-agent/r2-work-artifacts/other-worker.json"
        self.assertEqual("PINNED_ARTIFACT_GITHUB_METADATA_INVALID",self.verify(payload).reason)

    def test_boolean_negative_or_oversized_sizes_fail(self):
        for n in (True,-1,0,32769,10000000,"100"):
            payload,_=fixture()
            payload["size"]=n
            with self.subTest(n=n):
                self.assertEqual("PINNED_ARTIFACT_GITHUB_METADATA_INVALID",self.verify(payload).reason)

    def test_length_metadata_mismatch_fails(self):
        payload,_=fixture()
        payload["size"]+=1
        self.assertEqual("PINNED_ARTIFACT_SIZE_MISMATCH",self.verify(payload).reason)

    def test_base64_alphabet_invalid_refused(self):
        payload,_=fixture()
        payload["content"]="###INVALID###"
        self.assertIn(self.verify(payload).reason,(
            "PINNED_ARTIFACT_BODY_INVALID","PINNED_ARTIFACT_SIZE_MISMATCH",
        ))

    def test_wrong_assignment_is_not_verified(self):
        payload,digest=fixture(assignment_id="assignment-other")
        r=self.verify(payload,expected_artifact_sha256=digest)
        self.assertEqual("PINNED_ARTIFACT_ASSIGNMENT_SCOPE_INVALID",r.reason)

    def test_wrong_generation_or_boolean_version_refused(self):
        for version in (1,True,"0"):
            payload,digest=fixture(state_version=version)
            with self.subTest(version=version):
                r=self.verify(payload,expected_artifact_sha256=digest)
                self.assertEqual("PINNED_ARTIFACT_ASSIGNMENT_SCOPE_INVALID",r.reason)

    def test_extra_completion_or_send_authority_field_refused(self):
        payload,digest=fixture(browser_send_authorized=True)
        r=self.verify(payload,expected_artifact_sha256=digest)
        self.assertEqual("PINNED_ARTIFACT_SCHEMA_INVALID",r.reason)

    def test_fake_terminal_event_claim_is_denied(self):
        payload,digest=fixture(evidence_status="TURN_FINAL_CONFIRMED")
        r=self.verify(payload,expected_artifact_sha256=digest)
        self.assertEqual("PINNED_ARTIFACT_ASSIGNMENT_SCOPE_INVALID",r.reason)
        self.never_authorize(r)

    def test_forged_type_url_or_unexpected_json_schema_refused(self):
        payload,digest=fixture(kind="WAKE_MASTER")
        r=self.verify(payload,expected_artifact_sha256=digest)
        self.assertEqual("PINNED_ARTIFACT_ASSIGNMENT_SCOPE_INVALID",r.reason)

    def test_nonexistent_blob_does_not_create_source_files(self):
        r=self.verify(reader=lambda *_:None)
        self.assertEqual("PINNED_ARTIFACT_UNAVAILABLE",r.reason)
        self.never_authorize(r)

    def test_reader_exception_redacts_credentials(self):
        def bad(*args):
            raise OSError("PRIVATE_COOKIE https://chatgpt.com/c/private")
        r=self.verify(reader=bad)
        self.assertEqual("PINNED_ARTIFACT_GET_FAILED",r.reason)
        self.assertNotIn("PRIVATE_COOKIE",repr(r))

    def test_gh_native_argv_is_pinned_get_with_no_post(self):
        observed={}
        obj,_=fixture()
        class FakePopen:
            def __init__(self,argv,**options):
                observed["argv"]=argv
                observed["options"]=options
                options["stdout"].write(json.dumps(obj).encode())
                options["stdout"].flush()
            def wait(self,timeout):
                observed["timeout"]=timeout
                return 0
            def kill(self):
                raise AssertionError("not timeout")
        with patch("master_a_dynamic_v4.isolated_gh_immutable_artifact_v4.shutil.which",return_value="gh.exe"):
            with patch("master_a_dynamic_v4.isolated_gh_immutable_artifact_v4.subprocess.Popen",FakePopen):
                got=_native_gh_pinned_file(PATH,COMMIT)
        self.assertEqual(obj,got)
        self.assertEqual("gh.exe",observed["argv"][0])
        self.assertEqual("api",observed["argv"][1])
        self.assertEqual("github.com",observed["argv"][3])
        self.assertEqual("repos/Scorp96/6/contents/"+PATH+"?ref="+COMMIT,observed["argv"][-1])
        self.assertEqual(12,observed["timeout"])
        self.assertNotIn("POST",observed["argv"])
        self.assertEqual(subprocess.DEVNULL,observed["options"]["stdin"])

    def test_nonzero_native_api_drops_page_text(self):
        class FailPopen:
            def __init__(self,argv,**opts):
                opts["stdout"].write(b'{"secret":"PRIVATE_CREDENTIAL"}')
                opts["stderr"].write(b'PRIVATE_CREDENTIAL')
            def wait(self,timeout):return 1
            def kill(self):pass
        with patch("master_a_dynamic_v4.isolated_gh_immutable_artifact_v4.shutil.which",return_value="gh.exe"):
            with patch("master_a_dynamic_v4.isolated_gh_immutable_artifact_v4.subprocess.Popen",FailPopen):
                r=_native_gh_pinned_file(PATH,COMMIT)
        self.assertIsNone(r)

    def test_native_cli_without_gh_fails_closed(self):
        with patch("master_a_dynamic_v4.isolated_gh_immutable_artifact_v4.shutil.which",return_value=None):
            self.assertIsNone(_native_gh_pinned_file(PATH,COMMIT))

    def test_read_only_native_cli_never_offers_browser_mutations(self):
        import master_a_dynamic_v4.isolated_gh_immutable_artifact_v4 as module
        for name in ("send","tab_new","browser_submit","wake_master","dispatch_worker","reset_sqlite"):
            self.assertFalse(hasattr(module,name))


if __name__ == "__main__":
    unittest.main()
