from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v4_web_gpt_packet.py"


def load_packet():
    spec = importlib.util.spec_from_file_location("v4_web_gpt_packet", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class V4WebGptPacketTests(unittest.TestCase):
    def test_current_release_record_has_no_case_insensitive_duplicate_keys(self):
        repo_root = pathlib.Path(__file__).resolve().parents[3]
        release_path = repo_root / "docs" / "handoffs" / "SCORP_V4_RELEASE_RECORD_B0BBC3A.json"

        def reject_casefold_duplicates(pairs):
            seen = set()
            result = {}
            for key, value in pairs:
                folded = key.casefold()
                if folded in seen:
                    raise AssertionError(f"duplicate case-insensitive release key: {key}")
                seen.add(folded)
                result[key] = value
            return result

        json.loads(release_path.read_text(encoding="utf-8"), object_pairs_hook=reject_casefold_duplicates)

    def _fixture(self, root: pathlib.Path, *, candidate: str = "a" * 40):
        (root / "GPT_START_HERE.md").write_text("start\n", encoding="utf-8")
        (root / "scripts").mkdir()
        (root / "scripts" / "run-candidate-validation.ps1").write_text("# test\n", encoding="utf-8")
        bridge = root / "scorp-agent" / "chatgpt-gui-bridge"
        (root / "scorp-agent" / "master_a_dynamic_v4").mkdir(parents=True)
        (bridge / "tools").mkdir(parents=True)
        for name in (
            "v4_master_controller_runtime.py",
            "v4_master_supervisor_runtime.py",
            "v4_web_gpt_packet.py",
        ):
            (bridge / "tools" / name).write_text("# test\n", encoding="utf-8")
        (root / "docs" / "handoffs").mkdir(parents=True)
        (root / "docs" / "handoffs" / "SCORP_V4_GIT6_VALIDATION.json").write_text(
            json.dumps({"validated_commit": candidate}), encoding="utf-8"
        )
        (root / "docs" / "handoffs" / "SCORP_V4_WEB_GPT_HANDOFF.json").write_text(
            json.dumps({
                "candidate_commit": candidate,
                "target_repository": "Scorp96/6",
                "authority": {"git": "evidence_only"},
                "evidence_binding": {"validated_commit": candidate},
            }),
            encoding="utf-8",
        )
        (root / "docs" / "handoffs" / "SCORP_V4_WEB_GPT_HANDOFF.md").write_text(
            f"candidate {candidate}\n", encoding="utf-8"
        )
        database = root / "state.sqlite3"
        database.write_bytes(b"sqlite-placeholder")
        return database

    def test_packet_contains_preflight_and_pasteable_web_gpt_boundary(self):
        packet = load_packet()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = self._fixture(root)
            result = packet.build_packet(root, database_path=database, allowed_root=root)
            self.assertEqual("READY", result["status"])
            self.assertEqual("UNAVAILABLE", result["preflight"]["capabilities"]["web_gpt_direct_local_control"]["status"])
            self.assertIn("WEB_GPT_DIRECT_LOCAL_CONTROL_UNAVAILABLE", result["prompt"])
            self.assertIn("preflight", result)

    def test_packet_prompt_candidate_identity_matches_packet_candidate(self):
        packet = load_packet()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = self._fixture(root)
            result = packet.build_packet(root, database_path=database, allowed_root=root)
            candidate = result["candidate_commit"]
            self.assertIn(f"candidate commit {candidate}", result["prompt"])
            self.assertNotRegex(result["prompt"].replace(candidate, ""), r"[0-9a-f]{40}")

    def test_packet_blocks_when_handoff_and_validation_candidates_differ(self):
        packet = load_packet()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = self._fixture(root)
            handoff = json.loads((root / "docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.json").read_text())
            handoff["candidate_commit"] = "b" * 40
            (root / "docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.json").write_text(json.dumps(handoff), encoding="utf-8")
            result = packet.build_packet(root, database_path=database, allowed_root=root)
            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual("CANDIDATE_VERSION_MISMATCH", result["blockers"][0]["code"])

    def test_packet_blocks_when_declared_manifest_binds_a_different_candidate(self):
        packet = load_packet()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = self._fixture(root)
            manifest_path = root / "docs" / "handoffs" / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "format": "scorp-v4-candidate-manifest/1",
                        "candidate_commit": "b" * 40,
                        "source_tree": str(root),
                        "files": {"placeholder": {"sha256": "0" * 64, "size": 0}},
                        "manifest_sha256": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            handoff_path = root / "docs" / "handoffs" / "SCORP_V4_WEB_GPT_HANDOFF.json"
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            handoff["evidence_binding"]["candidate_manifest"] = "docs/handoffs/manifest.json"
            handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
            result = packet.build_packet(root, database_path=database, allowed_root=root)
            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual("CANDIDATE_MANIFEST_MISMATCH", result["blockers"][0]["code"])

    def test_packet_rejects_competing_current_release_records(self):
        packet = load_packet()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = self._fixture(root)
            release = {
                "code_candidate_sha": "a" * 40,
                "validation_record": {"path": "docs/handoffs/SCORP_V4_GIT6_VALIDATION.json"},
                "candidate_manifest": {"path": None},
            }
            handoffs = root / "docs" / "handoffs"
            (handoffs / "SCORP_V4_RELEASE_RECORD_A.json").write_text(json.dumps(release), encoding="utf-8")
            (handoffs / "SCORP_V4_RELEASE_RECORD_B.json").write_text(json.dumps(release), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "MULTIPLE_CURRENT_RELEASE_RECORDS"):
                packet.build_packet(root, database_path=database, allowed_root=root)

    def test_repository_startup_commands_use_current_candidate_manifest_hash(self):
        repo_root = pathlib.Path(__file__).resolve().parents[3]
        manifest_path = repo_root / "docs" / "handoffs" / "SCORP_V4_CANDIDATE_MANIFEST_B0BBC3A.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest["manifest_sha256"]
        startup = (repo_root / "GPT_START_HERE.md").read_text(encoding="utf-8")
        values = re.findall(r"--manifest-sha256\s+([0-9a-f]{64})", startup)
        self.assertGreaterEqual(len(values), 2)
        self.assertTrue(all(value == expected for value in values), (expected, values))


if __name__ == "__main__":
    unittest.main()
