from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from master_a_dynamic_v4.runtime_cli import main
from master_a_dynamic_v4.state_store import StateStore


class RuntimeCliTests(unittest.TestCase):
    def test_cli_processes_one_json_response_per_request_without_shell_surface(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "runtime.sqlite"
            store = StateStore(db, [root])
            store.create_contract("p1", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.acquire_daemon_lease("p1", "daemon")
            state_version = store.get_project_state("p1")["state_version"]
            store.close()
            requests = [
                {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "cli-status",
                    "command": "runtime.status",
                    "project_id": "p1",
                    "payload": {},
                },
                {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "cli-pause",
                    "command": "project.pause",
                    "project_id": "p1",
                    "expected_state_version": state_version,
                    "expected_daemon_epoch": 1,
                    "payload": {},
                },
                {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "cli-shell",
                    "command": "powershell.exe",
                    "project_id": "p1",
                    "payload": {"command": "whoami"},
                },
            ]
            stdin = io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdin", stdin), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main([
                    "--database-path", str(db), "--project-id", "p1", "--allowed-root", str(root),
                    "--daemon-epoch", "1",
                ])
            self.assertEqual(0, code)
            lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(3, len(lines))
            self.assertEqual("OK", lines[0]["status"])
            self.assertEqual("OK", lines[1]["status"])
            self.assertEqual("REJECTED", lines[2]["status"])
            self.assertEqual("UNKNOWN_COMMAND", lines[2]["error"]["code"])
            self.assertEqual("", stderr.getvalue())

    def test_cli_returns_machine_error_for_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "runtime.sqlite"
            store = StateStore(db, [root])
            store.close()
            stdin = io.StringIO("not-json\n")
            stdout = io.StringIO()
            with patch("sys.stdin", stdin), contextlib.redirect_stdout(stdout):
                main(["--database", str(db), "--project-id", "p1", "--allowed-root", str(root), "--daemon-epoch", "1"])
            result = json.loads(stdout.getvalue())
            self.assertEqual("REJECTED", result["status"])
            self.assertEqual("INVALID_JSON", result["error"]["code"])


if __name__ == "__main__":
    unittest.main()
