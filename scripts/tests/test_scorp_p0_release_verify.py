"""Isolated release-preflight proofs: no installed SCORP files are touched."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "scorp_p0_release_verify.py"
SPEC = importlib.util.spec_from_file_location("scorp_p0_release_verify", SOURCE)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        files = {}
        for i, name in enumerate(sorted(verify.REQUIRED_PATHS)):
            file_path = self.root / name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            data = f"SCORP isolated fixture {i}\n".encode()
            file_path.write_bytes(data)
            files[name] = {
                "git_blob_sha": verify._git_blob_sha1(data),
                "sha256": __import__("hashlib").sha256(data).hexdigest(),
            }
        self.manifest = self.root / "scorp-agent" / "release-manifest-v4.json"
        self.manifest_obj = {"protocol_version": verify.PROTOCOL, "files": files}
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.manifest_obj, sort_keys=True),
                                 encoding="utf-8")

    def check(self):
        return verify.evaluate_manifest(self.root, self.manifest)

    def test_all_files_with_exact_dual_hashes_ready_only_for_manual_review(self):
        report = self.check()
        self.assertEqual("READY_FOR_MANUAL_RELEASE_REVIEW", report["result"])
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["production_modified"])
        self.assertEqual(7, report["total_files"])
        self.assertEqual(0, report["blocked_files"])

    def test_one_changed_blob_is_blocked(self):
        first = sorted(verify.REQUIRED_PATHS)[0]
        (self.root / first).write_bytes(b"tampered after manifest\n")
        report = self.check()
        self.assertEqual("BLOCKED", report["result"])
        self.assertEqual(1, report["blocked_files"])
        self.assertEqual("MISMATCH", next(c["git_blob"] for c in report["checks"] if c["path"] == first))

    def test_bootstrap_computed_sha256_placeholder_is_not_release_authority(self):
        first = sorted(verify.REQUIRED_PATHS)[0]
        self.manifest_obj["files"][first]["sha256"] = "computed-at-bootstrap"
        self.save_manifest()
        report = self.check()
        self.assertEqual("BLOCKED", report["result"])
        self.assertEqual("UNPINNED", next(c["sha256"] for c in report["checks"] if c["path"] == first))

    def test_wrong_sha256_with_correct_git_blob_is_blocked(self):
        first = sorted(verify.REQUIRED_PATHS)[0]
        self.manifest_obj["files"][first]["sha256"] = "f" * 64
        self.save_manifest()
        self.assertEqual("BLOCKED", self.check()["result"])

    def test_manifest_unknown_or_missing_files_is_rejected(self):
        self.manifest_obj["files"].pop(sorted(verify.REQUIRED_PATHS)[0])
        self.save_manifest()
        with self.assertRaisesRegex(verify.ReleaseManifestRejected, "MANIFEST_FILE_SET_INVALID"):
            self.check()

    def test_duplicate_json_keys_cannot_override_pinned_authority(self):
        self.manifest.write_text(
            '{"protocol_version":"' + verify.PROTOCOL + '","protocol_version":"overwrite","files":{}}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verify.ReleaseManifestRejected, "MANIFEST_DUPLICATE_KEY"):
            self.check()

    def test_manifest_outside_checked_out_repository_fails_closed(self):
        outside = self.root.parent / ("outside-" + self.root.name + ".json")
        # Do not create a real outside file; resolve(strict=True) must fail.
        with self.assertRaises((verify.ReleaseManifestRejected, FileNotFoundError)):
            verify.evaluate_manifest(self.root, outside)

    def test_symlinked_source_refused_when_platform_supports_symlinks(self):
        path = self.root / sorted(verify.REQUIRED_PATHS)[0]
        target = path.with_suffix(".symlink-target")
        target.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable on this host")
        with self.assertRaisesRegex(verify.ReleaseManifestRejected, "MANIFEST_SOURCE_SYMLINK"):
            self.check()

    def test_git_blob_mode_reads_committed_bytes_not_windows_crlf_checkout(self):
        # Prove the release gate is anchored to pinned Git objects, not
        # Windows worktree autocrlf or an uncommitted manifest mutation.
        def git(*args):
            return subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True, check=True, timeout=20, text=True,
            ).stdout.strip()

        try:
            git("init", "-q")
            git("config", "user.name", "SCORP Isolated Test")
            git("config", "user.email", "scorp-fixture@example.invalid")
            git("add", "scorp-agent")
            git("commit", "-qm", "isolated release source fixture")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git binary unavailable for offline Git blob fixture")

        name = sorted(verify.REQUIRED_PATHS)[0]
        target = self.root / name
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual("BLOCKED", self.check()["result"])
        git_report = verify.evaluate_manifest(self.root, self.manifest, source_mode="git")
        self.assertEqual("READY_FOR_MANUAL_RELEASE_REVIEW", git_report["result"])
        self.assertEqual("git", git_report["source_mode"])
        self.assertFalse(git_report["release_authorized"])

        # An uncommitted edit to the manifest cannot update authorized hashes.
        self.manifest_obj["files"][name]["git_blob_sha"] = "0" * 40
        self.save_manifest()
        git_report = verify.evaluate_manifest(self.root, self.manifest, source_mode="git")
        self.assertEqual("READY_FOR_MANUAL_RELEASE_REVIEW", git_report["result"])

    def test_cli_exit_code_2_and_machine_readable_block(self):
        first = sorted(verify.REQUIRED_PATHS)[0]
        self.manifest_obj["files"][first]["git_blob_sha"] = "0" * 40
        self.save_manifest()
        run = subprocess.run(
            [sys.executable, str(SOURCE), "--repo-root", str(self.root), "--source-mode", "filesystem"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(2, run.returncode, run.stderr)
        response = json.loads(run.stdout)
        self.assertEqual("BLOCKED", response["result"])
        self.assertFalse(response["release_authorized"])


if __name__ == "__main__":
    unittest.main()
