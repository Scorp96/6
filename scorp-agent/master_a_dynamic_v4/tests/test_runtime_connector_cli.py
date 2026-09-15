from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from master_a_dynamic_v4.runtime_connector_cli import RuntimeConnectorSession


class RuntimeConnectorCliTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.session = RuntimeConnectorSession(self.client, actor="gpt-master", project_id="p1")

    def test_actor_is_bound_before_transport_call(self):
        line = json.dumps(
            {
                "protocol_version": "scorp.runtime.command/1",
                "request_id": "connector-actor-1",
                "command": "runtime.status",
                "project_id": "p1",
                "actor": "untrusted-web-text",
                "payload": {},
            }
        )
        response = self.session.handle_line(line)
        self.assertEqual("REJECTED", response["status"])
        self.assertEqual("ACTOR_SCOPE_MISMATCH", response["error"]["code"])
        self.client.request.assert_not_called()

    def test_valid_request_is_forwarded_once(self):
        request = {
            "protocol_version": "scorp.runtime.command/1",
            "request_id": "connector-1",
            "command": "runtime.status",
            "project_id": "p1",
            "actor": "gpt-master",
            "payload": {},
        }
        expected = {"protocol_version": "scorp.runtime.response/1", "status": "OK", "request_id": "connector-1"}
        self.client.request.return_value = expected
        self.assertEqual(expected, self.session.handle_line(json.dumps(request)))
        self.client.request.assert_called_once()

    def test_malformed_request_fails_closed_without_transport(self):
        response = self.session.handle_line("not-json")
        self.assertEqual("REJECTED", response["status"])
        self.assertEqual("INVALID_JSON", response["error"]["code"])
        self.client.request.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows Named Pipe connector")
    def test_connector_process_round_trips_through_named_pipe(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = root / "runtime.sqlite"
            store = __import__("master_a_dynamic_v4.state_store", fromlist=["StateStore"]).StateStore(database, [root])
            store.create_contract(
                "p1", root_contract={"objective": "connector-process"}, acceptance_contract={"required": []}
            )
            lease = store.acquire_daemon_lease("p1", "connector-process-daemon")
            store.close()

            authkey = "scorp-connector-process-authkey"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[2])
            env["SCORP_TEST_PIPE_AUTHKEY"] = authkey
            python = r"C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe"
            server = subprocess.Popen(
                [
                    python,
                    "-B",
                    "-m",
                    "master_a_dynamic_v4.runtime_pipe_cli",
                    "--database",
                    str(database),
                    "--project-id",
                    "p1",
                    "--allowed-root",
                    str(root),
                    "--daemon-epoch",
                    str(lease["daemon_epoch"]),
                    "--authkey-env",
                    "SCORP_TEST_PIPE_AUTHKEY",
                    "--once",
                ],
                cwd=str(pathlib.Path(__file__).resolve().parents[2]),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            request = {
                "protocol_version": "scorp.runtime.command/1",
                "request_id": "connector-process-1",
                "command": "runtime.status",
                "project_id": "p1",
                "actor": "gpt-master",
                "payload": {},
            }
            try:
                time.sleep(0.2)
                connector = subprocess.run(
                    [
                        python,
                        "-B",
                        "-m",
                        "master_a_dynamic_v4.runtime_connector_cli",
                        "--project-id",
                        "p1",
                        "--authkey-env",
                        "SCORP_TEST_PIPE_AUTHKEY",
                        "--timeout-seconds",
                        "3",
                    ],
                    cwd=str(pathlib.Path(__file__).resolve().parents[2]),
                    env=env,
                    input=json.dumps(request) + "\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10,
                )
            finally:
                try:
                    server_stdout, server_stderr = server.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server_stdout, server_stderr = server.communicate(timeout=10)
                    self.fail(f"pipe server did not exit: stdout={server_stdout!r} stderr={server_stderr!r}")
            self.assertEqual(0, connector.returncode, connector.stderr)
            response = json.loads(connector.stdout)
            self.assertEqual("OK", response["status"])
            self.assertEqual("p1", response["project_id"])
            self.assertEqual(0, server.returncode, server_stderr)


if __name__ == "__main__":
    unittest.main()
