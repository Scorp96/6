from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v4_master_controller_runtime.py"


def load_runtime():
    spec = importlib.util.spec_from_file_location("v4_master_controller_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MasterControllerRuntimeTests(unittest.TestCase):
    def test_parser_accepts_only_structured_work_result_json(self):
        runtime = load_runtime()
        value = {
            "work_result_version": "1",
            "task_id": "T1",
            "status": "COMPLETE",
        }
        snapshot = "#### ChatGPT said:\n```json\n" + json.dumps(value) + "\n```"
        self.assertEqual(value, runtime.parse_structured_response(snapshot, "intent-1"))
        self.assertIsNone(runtime.parse_structured_response("#### ChatGPT said:\nfinished", "intent-1"))
        self.assertIsNone(runtime.parse_structured_response("#### ChatGPT said:\n{} trailing", "intent-1"))

    def test_runtime_requires_explicit_send_gate_before_opening_browser(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            args = runtime.build_parser().parse_args(
                [
                    "--plan-json", str(root / "plan.json"),
                    "--database-path", str(root / "state.sqlite3"),
                    "--driver-state-path", str(root / "driver.json"),
                    "--allowed-root", str(root),
                ]
            )
            self.assertEqual(2, runtime.run_runtime(args))
            self.assertFalse((root / "state.sqlite3").exists())
            self.assertFalse((root / "driver.json").exists())

    def test_send_requires_exact_candidate_manifest_before_opening_browser(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({
                    "root_contract": {"project_id": "scorp-v4-master-runtime", "root_objective": "test"},
                    "acceptance_contract": {"required": ["AC01"]},
                    "plan": {"project_id": "scorp-v4-master-runtime", "master_identity": "A", "tasks": []},
                }),
                encoding="utf-8",
            )
            args = runtime.build_parser().parse_args(
                [
                    "--send",
                    "--plan-json", str(plan),
                    "--database-path", str(root / "state.sqlite3"),
                    "--driver-state-path", str(root / "driver.json"),
                    "--allowed-root", str(root),
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "CANDIDATE_BINDING_REQUIRED"):
                runtime.run_runtime(args)
            self.assertFalse((root / "state.sqlite3").exists())
            self.assertFalse((root / "driver.json").exists())

    def test_candidate_binding_accepts_canonical_manifest_hash(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            core = {
                "format": "scorp-v4-candidate-manifest/1",
                "candidate_commit": "a" * 40,
                "source_tree": str(root),
                "files": {"example.py": {"sha256": "b" * 64, "size": 1}},
            }
            from master_a_dynamic_v4.models import sha256_json
            manifest = {**core, "manifest_sha256": sha256_json(core)}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            binding = runtime.validate_candidate_binding(
                path,
                candidate_commit="a" * 40,
                manifest_sha256=manifest["manifest_sha256"],
            )
            self.assertEqual(manifest["manifest_sha256"], binding["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
