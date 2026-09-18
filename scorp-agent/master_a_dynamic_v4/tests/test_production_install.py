from __future__ import annotations

import hashlib
import pathlib
import tempfile
import unittest

from master_a_dynamic_v4.install_manifest import (
    InstallIdentityError,
    install_runtime_release,
    verify_runtime_release,
)
from master_a_dynamic_v4.models import sha256_json
from master_a_dynamic_v4.production_bootstrap import (
    bootstrap_production_authority,
    verify_production_authority,
)


CANDIDATE = "b" * 40


def candidate_manifest(source: pathlib.Path) -> dict:
    rel = "scorp-agent/master_a_dynamic_v4/payload.py"
    path = source / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    metadata = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
    core = {
        "format": "scorp-v4-candidate-manifest/1",
        "candidate_commit": CANDIDATE,
        "source_tree": str(source.resolve()),
        "files": {rel: metadata},
    }
    return {**core, "manifest_sha256": sha256_json(core)}


class ProductionInstallTests(unittest.TestCase):
    def test_runtime_release_is_code_only_and_hash_verified(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            source = base / "source"
            source.mkdir()
            manifest = candidate_manifest(source)
            release = base / "releases" / CANDIDATE
            protected = base / "protected"
            protected.mkdir()
            (protected / "keep.txt").write_text("unchanged", encoding="utf-8")
            receipt = install_runtime_release(
                source,
                release,
                manifest,
                protected_roots=[protected],
                observed_commit=CANDIDATE,
            )
            self.assertEqual(CANDIDATE, receipt["candidate_commit"])
            self.assertFalse((release / "state.sqlite3").exists())
            verify_runtime_release(release, receipt, manifest)
            installed = release / "scorp-agent/master_a_dynamic_v4/payload.py"
            installed.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallIdentityError, "INSTALLED_FILE_HASH_MISMATCH"):
                verify_runtime_release(release, receipt, manifest)

    def test_bootstrap_creates_one_explicit_canonical_authority_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            source = base / "source"
            source.mkdir()
            manifest = candidate_manifest(source)
            release = base / "releases" / CANDIDATE
            install_runtime_release(source, release, manifest, observed_commit=CANDIDATE)
            authority = base / "active"
            kwargs = dict(
                release_root=release,
                manifest=manifest,
                project_id="scorp-v4-production",
                objective="Own durable SCORP production project life.",
                acceptance_ids=["AC_PERSISTENT_RUNTIME_OPERATIONAL"],
            )
            first = bootstrap_production_authority(authority, **kwargs)
            second = bootstrap_production_authority(authority, **kwargs)
            self.assertEqual(first, second)
            observed = verify_production_authority(
                authority,
                release_root=release,
                manifest=manifest,
                expected_project_id="scorp-v4-production",
            )
            self.assertEqual("ACTIVE", observed["status"])
            self.assertEqual(0, observed["master_epoch"])
            self.assertTrue((authority / "state.sqlite3").is_file())
            self.assertTrue((authority / "workspace").is_dir())
            self.assertFalse((authority / "driver.json").exists())
            with self.assertRaisesRegex(InstallIdentityError, "AUTHORITY_CONTRACT_REQUEST_MISMATCH"):
                bootstrap_production_authority(
                    authority,
                    release_root=release,
                    manifest=manifest,
                    project_id="scorp-v4-production",
                    objective="Different objective must not be silently adopted.",
                    acceptance_ids=["AC_PERSISTENT_RUNTIME_OPERATIONAL"],
                )

    def test_bootstrap_refuses_to_adopt_preexisting_unreceipted_state(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            source = base / "source"
            source.mkdir()
            manifest = candidate_manifest(source)
            release = base / "release"
            install_runtime_release(source, release, manifest, observed_commit=CANDIDATE)
            authority = base / "active"
            authority.mkdir()
            (authority / "unknown.txt").write_text("legacy", encoding="utf-8")
            with self.assertRaisesRegex(InstallIdentityError, "AUTHORITY_ROOT_NOT_EMPTY"):
                bootstrap_production_authority(
                    authority,
                    release_root=release,
                    manifest=manifest,
                    project_id="scorp-v4-production",
                    objective="Own durable production life.",
                )

    def test_installer_uses_release_verifying_runtime_launcher(self):
        installer = pathlib.Path(__file__).resolve().parents[2] / "chatgpt-gui-bridge" / "install-v4-daemon.ps1"
        text = installer.read_text(encoding="utf-8")
        self.assertIn("tools\\v4_release_runtime.py", text)
        self.assertNotIn("tools\\v4_daemon_runtime.py'))\n    if (-not (Test-Path -LiteralPath $daemonScript", text)


if __name__ == "__main__":
    unittest.main()
