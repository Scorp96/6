from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


COMMIT = "a" * 40


class ReleaseIdentityTests(unittest.TestCase):
    def make_source(self, root: pathlib.Path) -> pathlib.Path:
        source = root / "source"
        (source / "pkg").mkdir(parents=True)
        (source / "pkg" / "core.py").write_text("VALUE = 4\n", encoding="utf-8")
        (source / "schema.sql").write_text("CREATE TABLE sample(id INTEGER);\n", encoding="utf-8")
        (source / "ignored.txt").write_text("must not be installed\n", encoding="utf-8")
        return source

    def test_candidate_commit_mismatch_changed_hash_and_path_escape_fail_closed(self):
        from master_a_dynamic_v4.install_manifest import (
            InstallIdentityError,
            build_candidate_manifest,
            verify_candidate_manifest,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.make_source(root)
            with self.assertRaisesRegex(InstallIdentityError, "CANDIDATE_COMMIT_MISMATCH"):
                build_candidate_manifest(source, COMMIT, ["pkg/core.py"], observed_commit="b" * 40)
            with self.assertRaisesRegex(InstallIdentityError, "SOURCE_PATH_ESCAPE"):
                build_candidate_manifest(source, COMMIT, ["../outside.py"], observed_commit=COMMIT)
            manifest = build_candidate_manifest(
                source, COMMIT, ["pkg/core.py", "schema.sql"], observed_commit=COMMIT
            )
            (source / "pkg" / "core.py").write_text("VALUE = 5\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallIdentityError, "SOURCE_FILE_HASH_MISMATCH"):
                verify_candidate_manifest(source, manifest, expected_commit=COMMIT, observed_commit=COMMIT)

    def test_lab_install_is_allowlisted_and_protected_paths_remain_byte_identical(self):
        from master_a_dynamic_v4.install_manifest import (
            build_candidate_manifest,
            install_to_lab,
            snapshot_paths,
            verify_install_receipt,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.make_source(root)
            lab = root / "lab"
            protected = root / "production"
            protected.mkdir()
            (protected / "state.json").write_text('{"authority":"v3"}\n', encoding="utf-8")
            before = snapshot_paths([protected])
            manifest = build_candidate_manifest(
                source, COMMIT, ["pkg/core.py", "schema.sql"], observed_commit=COMMIT
            )
            receipt = install_to_lab(
                source,
                lab,
                manifest,
                protected_roots=[protected],
                interpreter=pathlib.Path(sys.executable).name,
                observed_commit=COMMIT,
            )
            after = snapshot_paths([protected])
            self.assertEqual(before, after)
            self.assertTrue((lab / "pkg" / "core.py").is_file())
            self.assertTrue((lab / "schema.sql").is_file())
            self.assertFalse((lab / "ignored.txt").exists())
            self.assertEqual(COMMIT, receipt["source_commit"])
            self.assertEqual(str(source.resolve()), receipt["source_tree"])
            expected_interpreter = pathlib.Path(shutil.which(pathlib.Path(sys.executable).name)).resolve()
            self.assertEqual(str(expected_interpreter), receipt["interpreter"])
            self.assertEqual(str((lab / "state.sqlite3").resolve()), receipt["database"])
            self.assertEqual(manifest["manifest_sha256"], receipt["manifest_sha256"])
            verify_install_receipt(lab, receipt, manifest)
            altered = json.loads(json.dumps(receipt))
            altered["manifest_sha256"] = "0" * 64
            with self.assertRaisesRegex(Exception, "INSTALL_MANIFEST_MISMATCH"):
                verify_install_receipt(lab, altered, manifest)

    def test_protected_target_is_refused_and_powershell_wrapper_has_no_production_switch(self):
        from master_a_dynamic_v4.install_manifest import InstallIdentityError, build_candidate_manifest, install_to_lab

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.make_source(root)
            protected = root / "production"
            protected.mkdir()
            manifest = build_candidate_manifest(source, COMMIT, ["schema.sql"], observed_commit=COMMIT)
            with self.assertRaisesRegex(InstallIdentityError, "LAB_TARGET_PROTECTED"):
                install_to_lab(
                    source,
                    protected / "nested",
                    manifest,
                    protected_roots=[protected],
                    interpreter=sys.executable,
                    observed_commit=COMMIT,
                )
        script = pathlib.Path(__file__).parents[1] / "install-lab.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn(r"C:\ScorpAgent\v4-core-lab", text)
        self.assertNotIn("Register-ScheduledTask", text)
        self.assertNotIn("Set-Service", text)

    def test_manifest_binds_files_to_candidate_git_objects(self):
        from master_a_dynamic_v4.install_manifest import InstallIdentityError, build_candidate_manifest

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "source"
            source.mkdir()
            (source / "tool.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            subprocess.run(["git", "add", "tool.py"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
            (source / "tool.py").write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallIdentityError, "SOURCE_FILE_NOT_AT_CANDIDATE"):
                build_candidate_manifest(source, commit, ["tool.py"])


if __name__ == "__main__":
    unittest.main()
