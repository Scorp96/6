from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


class V4DaemonRuntimeTests(unittest.TestCase):
    def test_runtime_reads_sqlite_and_fails_closed_when_master_rebind_handler_is_absent(self):
        from master_a_dynamic_v4.state_store import StateStore
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            store = StateStore(db, [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.close()
            args = runtime.build_parser().parse_args(
                [
                    "--database-path", str(db),
                    "--allowed-root", str(root),
                    "--project-id", "p",
                    "--daemon-epoch", "1",
                    "--health-path", str(root / "health.json"),
                    "--max-iterations", "1",
                ]
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = runtime.run_runtime(args)
            self.assertEqual(2, rc)
            summary = json.loads(output.getvalue())
            self.assertEqual("RESUME_MASTER", summary["decisions"][0]["action"])
            self.assertEqual("BLOCKED", summary["status"])
            self.assertTrue((root / "health.json").is_file())

    def test_runtime_acquires_one_sqlite_daemon_lease_and_fences_second_owner(self):
        from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            store = StateStore(db, [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.close()
            common = [
                "--database-path", str(db), "--allowed-root", str(root),
                "--project-id", "p", "--daemon-epoch", "1", "--max-iterations", "1",
            ]
            first = runtime.build_parser().parse_args(common + ["--actor-id", "daemon-a", "--health-path", str(root / "a.json")])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2, runtime.run_runtime(first))
            second = runtime.build_parser().parse_args(common + ["--actor-id", "daemon-b", "--health-path", str(root / "b.json")])
            with self.assertRaisesRegex(StoreInvariantError, "DAEMON_LEASE_ACTIVE"):
                with contextlib.redirect_stdout(io.StringIO()):
                    runtime.run_runtime(second)


if __name__ == "__main__":
    unittest.main()
