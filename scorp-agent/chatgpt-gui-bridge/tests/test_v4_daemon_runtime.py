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
                    "--daemon-epoch", "4",
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


if __name__ == "__main__":
    unittest.main()
