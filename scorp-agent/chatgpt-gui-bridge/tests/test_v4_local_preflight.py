from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v4_local_preflight.py"


def load_preflight():
    spec = importlib.util.spec_from_file_location("v4_local_preflight", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class V4LocalPreflightTests(unittest.TestCase):
    def test_report_distinguishes_web_only_from_ready_offline_and_monitor_modes(self):
        preflight = load_preflight()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "GPT_START_HERE.md").write_text("start\n", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "run-candidate-validation.ps1").write_text("# test\n", encoding="utf-8")
            (root / "scorp-agent" / "master_a_dynamic_v4").mkdir(parents=True)
            bridge = root / "scorp-agent" / "chatgpt-gui-bridge"
            (bridge / "tools").mkdir(parents=True)
            (bridge / "tools" / "v4_master_controller_runtime.py").write_text("# test\n", encoding="utf-8")
            (bridge / "tools" / "v4_master_supervisor_runtime.py").write_text("# test\n", encoding="utf-8")
            (bridge / "tools" / "v4_web_gpt_packet.py").write_text("# test\n", encoding="utf-8")
            database = root / "state.sqlite3"
            database.write_bytes(b"sqlite-placeholder")
            report = preflight.build_report(
                root,
                python_executable=pathlib.Path(__file__).resolve(),
                database_path=database,
                allowed_root=root,
            )
            self.assertEqual("READY", report["overall_status"])
            self.assertEqual("UNAVAILABLE", report["capabilities"]["web_gpt_direct_local_control"]["status"])
            self.assertEqual("READY", report["capabilities"]["offline_validation"]["status"])
            self.assertEqual("READY", report["capabilities"]["sqlite_master_monitor"]["status"])
            self.assertEqual("BLOCKED_MISSING_BROWSER_CONFIG", report["capabilities"]["browser_rebind"]["status"])
            self.assertTrue(report["required_paths"]["scorp-agent/chatgpt-gui-bridge/tools/v4_web_gpt_packet.py"])

    def test_cli_outputs_json_and_fails_closed_for_missing_repository_files(self):
        preflight = load_preflight()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            report = preflight.build_report(root, python_executable=pathlib.Path(__file__).resolve())
            self.assertEqual("BLOCKED", report["overall_status"])
            self.assertEqual("MISSING_REPOSITORY_FILES", report["blockers"][0]["code"])
            encoded = json.dumps(report, ensure_ascii=False)
            self.assertIn("web_gpt_direct_local_control", encoded)


if __name__ == "__main__":
    unittest.main()
