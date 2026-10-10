"""Proof of immutable Git-source SHA pins, no-write planning, and stale ref fencing."""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from scorp_p0_release_pin_plan import build_pin_plan
from scorp_p0_release_verify import (
    PROTOCOL, REQUIRED_PATHS, _git_blob_sha1, ReleaseManifestRejected,
)


class PinPlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.files = {}
        for name in sorted(REQUIRED_PATHS):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = ("source=" + name + "\n").encode("utf-8")
            target.write_bytes(data)
            self.files[name] = {
                "git_blob_sha": _git_blob_sha1(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        existing = {
            name: {"git_blob_sha": "0" * 40, "sha256": "computed-at-bootstrap"}
            for name in sorted(REQUIRED_PATHS)
        }
        self.manifest = self.root / "scorp-agent" / "release-manifest-v4.json"
        self.manifest.write_text(
            json.dumps({"protocol_version": PROTOCOL, "files": existing}),
            encoding="utf-8",
        )
        self.git("init", "-q")
        self.git("config", "user.name", "SCORP Release Fixture")
        self.git("config", "user.email", "review-only@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "offline sealed source fixture")
        self.head = self.git("rev-parse", "HEAD")

    def git(self, *args):
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=True, timeout=20,
        )
        return proc.stdout.strip()

    def test_exact_source_commit_yields_only_review_plan(self):
        result = build_pin_plan(self.root, expected_head=self.head)
        self.assertEqual("scorp.p0-release-pin-review-plan/1", result["protocol"])
        self.assertEqual(self.head, result["candidate_source_commit_sha"])
        self.assertEqual(self.files, result["proposed_manifest"]["files"])
        self.assertEqual("BLOCKED", result["current_manifest_result"])
        self.assertEqual(7, result["current_manifest_blocked_files"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["operator_signoff_attested"])
        self.assertFalse(result["windows_host_attested"])

    def test_dirty_worktree_cannot_alter_immutable_release_source(self):
        first = sorted(REQUIRED_PATHS)[0]
        (self.root / first).write_bytes(b"tampered CRLF\r\n")
        result = build_pin_plan(self.root, expected_head=self.head)
        self.assertEqual(self.files[first], result["proposed_manifest"]["files"][first])
        self.assertEqual("BLOCKED", result["current_manifest_result"])

    def test_uncommitted_manifest_edit_does_not_change_pins(self):
        previous = build_pin_plan(self.root, expected_head=self.head)
        self.manifest.write_text("DANGEROUS UNCOMMITTED CHANGE", encoding="utf-8")
        result = build_pin_plan(self.root, expected_head=self.head)
        self.assertEqual(previous["proposed_manifest_sha256"], result["proposed_manifest_sha256"])

    def test_explicit_stale_commit_is_rejected(self):
        with self.assertRaisesRegex(ReleaseManifestRejected, "PIN_PLAN_EXPECTED_COMMIT_MISMATCH"):
            build_pin_plan(self.root, expected_head="a" * 40)

    def test_new_commit_invalidates_older_expected_head(self):
        path = self.root / sorted(REQUIRED_PATHS)[0]
        path.write_bytes(b"new committed reviewed source\n")
        self.git("add", ".")
        self.git("commit", "-qm", "updated source requires another review")
        with self.assertRaisesRegex(ReleaseManifestRejected, "PIN_PLAN_EXPECTED_COMMIT_MISMATCH"):
            build_pin_plan(self.root, expected_head=self.head)

    def test_no_repository_cannot_claim_valid_release_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ReleaseManifestRejected, "PIN_PLAN_GIT_HEAD_UNAVAILABLE"):
                build_pin_plan(temp)


if __name__ == "__main__":
    unittest.main()
