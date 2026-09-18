from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from tools import v4_release_runtime


class V4ReleaseRuntimeTests(unittest.TestCase):
    def test_missing_release_identity_fails_before_daemon_start(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            with mock.patch.object(v4_release_runtime, "RELEASE_ROOT", root):
                with mock.patch.object(v4_release_runtime, "run_runtime") as run:
                    with self.assertRaisesRegex(RuntimeError, "RUNTIME_RELEASE_IDENTITY_MISSING"):
                        v4_release_runtime.main([])
                    run.assert_not_called()

    def test_release_identity_is_verified_before_daemon_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manifest = {"candidate_commit": "a" * 40, "manifest_sha256": "b" * 64, "files": {"x": {"sha256": "c" * 64}}}
            receipt = {"format": "scorp-v4-runtime-release-install/1", "candidate_commit": "a" * 40, "manifest_sha256": "b" * 64, "release_root": str(root), "file_count": 1}
            (root / "candidate-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "release-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            authority = root / "active"
            authority.mkdir()
            database = authority / "state.sqlite3"
            database.write_bytes(b"sqlite-placeholder")
            events = []
            parser = mock.Mock()
            parsed = mock.Mock(database_path=database, project_id="scorp-v4-production")
            parser.parse_args.side_effect = lambda argv: events.append("parse") or parsed

            def verify(*args):
                events.append("verify-release")

            def verify_authority(*args, **kwargs):
                events.append("verify-authority")
                self.assertEqual(authority.resolve(), pathlib.Path(args[0]).resolve())
                self.assertEqual(root.resolve(), pathlib.Path(kwargs["release_root"]).resolve())
                self.assertEqual("scorp-v4-production", kwargs["expected_project_id"])

            def run(args):
                events.append("run")
                self.assertIs(parsed, args)
                return 7

            with mock.patch.object(v4_release_runtime, "RELEASE_ROOT", root), \
                 mock.patch.object(v4_release_runtime, "verify_runtime_release", side_effect=verify), \
                 mock.patch.object(v4_release_runtime, "verify_production_authority", side_effect=verify_authority), \
                 mock.patch.object(v4_release_runtime, "build_parser", return_value=parser), \
                 mock.patch.object(v4_release_runtime, "run_runtime", side_effect=run):
                self.assertEqual(7, v4_release_runtime.main(["--fake"]))
            self.assertEqual(["verify-release", "parse", "verify-authority", "run"], events)

    def test_stale_release_authority_mismatch_fails_before_daemon_run(self):
        from master_a_dynamic_v4.install_manifest import InstallIdentityError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            authority = root / "active"
            authority.mkdir()
            database = authority / "state.sqlite3"
            database.write_bytes(b"sqlite-placeholder")
            manifest = {
                "candidate_commit": "a" * 40,
                "manifest_sha256": "b" * 64,
                "files": {"x": {"sha256": "c" * 64}},
            }
            receipt = {
                "format": "scorp-v4-runtime-release-install/1",
                "candidate_commit": "a" * 40,
                "manifest_sha256": "b" * 64,
                "release_root": str(root),
                "file_count": 1,
            }
            (root / "candidate-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "release-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            parser = mock.Mock()
            parser.parse_args.return_value = mock.Mock(
                database_path=database,
                project_id="scorp-v4-production",
            )
            with mock.patch.object(v4_release_runtime, "RELEASE_ROOT", root), \
                 mock.patch.object(v4_release_runtime, "verify_runtime_release"), \
                 mock.patch.object(
                     v4_release_runtime,
                     "verify_production_authority",
                     side_effect=InstallIdentityError("AUTHORITY_RELEASE_ROOT_MISMATCH"),
                 ), \
                 mock.patch.object(v4_release_runtime, "build_parser", return_value=parser), \
                 mock.patch.object(v4_release_runtime, "run_runtime") as run:
                with self.assertRaisesRegex(InstallIdentityError, "AUTHORITY_RELEASE_ROOT_MISMATCH"):
                    v4_release_runtime.main(["--fake"])
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
