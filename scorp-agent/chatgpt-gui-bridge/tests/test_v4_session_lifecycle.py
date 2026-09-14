from __future__ import annotations

import asyncio
import json
import pathlib
import tempfile
import unittest

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from tools.v4_session_lifecycle import list_lifecycle, retire_lifecycle, write_evidence


class FakeCli:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    async def run_json(self, session, *args, timeout_seconds=30):
        self.calls.append((session, list(args), timeout_seconds))
        if not self.responses:
            raise AssertionError(f"unexpected call: {session} {args}")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def manifest_args(root: pathlib.Path) -> dict[str, object]:
    manifest = root / "manifest.json"
    fixture = root / "fixture.txt"
    fixture.write_text("x", encoding="utf-8")
    core = {
        "format": "scorp-v4-candidate-manifest/1",
        "candidate_commit": "a" * 40,
        "source_tree": str(root),
        "files": {"fixture.txt": {"sha256": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881", "size": 1}},
    }
    from master_a_dynamic_v4.models import sha256_json

    value = {**core, "manifest_sha256": sha256_json(core)}
    manifest.write_text(json.dumps(value), encoding="utf-8")
    return {
        "candidate_commit": value["candidate_commit"],
        "candidate_manifest": manifest,
        "manifest_sha256": value["manifest_sha256"],
    }


class V4SessionLifecycleCommandTests(unittest.TestCase):
    def test_list_is_read_only_and_marks_global_cleanup_forbidden(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            driver = ChromeUseActorDriverV3(FakeCli(), root / "driver.json")
            driver.register_session("diag-one", role="DIAGNOSTIC")
            report = list_lifecycle(root / "driver.json")
            self.assertEqual("READ_ONLY", report["status"])
            self.assertEqual("FORBIDDEN", report["global_cleanup"])
            self.assertEqual("DIAGNOSTIC", report["sessions"]["diag-one"]["role"])

    def test_retire_requires_exact_candidate_binding_before_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            driver = ChromeUseActorDriverV3(FakeCli(), root / "driver.json")
            driver.register_session("diag-two", role="DIAGNOSTIC")
            args = type("Args", (), {
                "session": "diag-two", "turn_id": None, "reason": "done", "stop": False,
                "allow_persistent": False, "executable": "unused",
                "candidate_commit": "a" * 40, "candidate_manifest": root / "missing.json",
                "manifest_sha256": "c" * 64, "driver_state_path": root / "driver.json",
            })()
            with self.assertRaisesRegex(RuntimeError, "CANDIDATE_MANIFEST_MISSING"):
                asyncio.run(retire_lifecycle(args, cli=FakeCli()))
            self.assertEqual("ACTIVE", driver.lifecycle_snapshot()["diag-two"]["status"])

    def test_retire_named_diagnostic_can_stop_only_that_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            cli = FakeCli([{"success": True}])
            driver = ChromeUseActorDriverV3(cli, root / "driver.json")
            driver.register_session("diag-three", role="DIAGNOSTIC")
            values = manifest_args(root)
            args = type("Args", (), {
                "session": "diag-three", "turn_id": None, "reason": "done", "stop": True,
                "allow_persistent": False, "executable": "unused", "driver_state_path": root / "driver.json",
                **values,
            })()
            result = asyncio.run(retire_lifecycle(args, cli=cli))
            self.assertEqual("STOPPED", result["result"]["cleanup"])
            self.assertEqual([["session", "stop"]], [call[1] for call in cli.calls])

    def test_global_prune_and_close_all_are_not_available(self):
        with tempfile.TemporaryDirectory() as td:
            parser_module = __import__("tools.v4_session_lifecycle", fromlist=["build_parser"])
            parser = parser_module.build_parser()
            with self.assertRaises(SystemExit):
                parser.parse_args(["retire", "--driver-state-path", td, "--reason", "x"])

    def test_receipt_writer_persists_a_hashable_audit_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "evidence.json"
            record = write_evidence(path, {"format": "test", "status": "READ_ONLY"})
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record, stored)
            self.assertEqual(64, len(stored["artifact_sha256"]))
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
