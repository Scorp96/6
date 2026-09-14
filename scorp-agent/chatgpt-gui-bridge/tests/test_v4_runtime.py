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
            self.assertEqual(2, payload["worker_capacity"])
            self.assertEqual("NOT_ATTEMPTED", payload["browser_io"])
            self.assertTrue(database.exists())
            self.assertNotIn("bridge_worker", completed.stdout)
            self.assertNotIn("scorp-control-plane", completed.stdout)


if __name__ == "__main__":
    unittest.main()
