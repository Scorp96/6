import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class V4RuntimeEntrypointTests(unittest.TestCase):
    def test_describe_is_sqlite_only_and_never_starts_browser(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / "worktree"
            worktree.mkdir()
            database = root / "state.sqlite3"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "v4_runtime.py"),
                    "--describe",
                    "--database-path",
                    str(database),
                    "--project-id",
                    "runtime-test",
                    "--allowed-root",
                    str(worktree),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual("sqlite", payload["queue_authority"])
            self.assertIsNone(payload["reasoning_model"])
            self.assertEqual("UNVERIFIED", payload["reasoning_model_verification"])
            self.assertEqual("GPT-5.6 Sol", payload["required_reasoning_model"])
            self.assertEqual("NOT_RUN", payload["model_policy_gate"])
            self.assertEqual(2, payload["worker_capacity"])
            self.assertEqual("NOT_ATTEMPTED", payload["browser_io"])
            self.assertTrue(database.exists())
            self.assertNotIn("bridge_worker", completed.stdout)
            self.assertNotIn("scorp-control-plane", completed.stdout)


    def test_untrusted_environment_cannot_attest_a_different_browser_model(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
            env = os.environ.copy()
            env["SCORP_REASONING_MODEL"] = "GPT-6"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "v4_runtime.py"),
                    "--describe",
                    "--database-path",
                    str(root / "status.sqlite3"),
                    "--project-id",
                    "unverified-model-test",
                    "--allowed-root",
                    str(root),
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            status = json.loads(result.stdout)
            self.assertIsNone(status["reasoning_model"])
            self.assertEqual("UNVERIFIED", status["reasoning_model_verification"])
            self.assertEqual("GPT-5.6 Sol", status["required_reasoning_model"])
            self.assertEqual("NOT_RUN", status["model_policy_gate"])
            self.assertEqual("NOT_ATTEMPTED", status["browser_io"])


if __name__ == "__main__":
    unittest.main()
