from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest

from master_a_dynamic_v4.runtime_pipe import pipe_name
from master_a_dynamic_v4.runtime_pipe_client import RuntimePipeClient
from master_a_dynamic_v4.state_store import StateStore


class RuntimePipeClientTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows Named Pipe transport")
    def test_client_round_trips_one_bounded_runtime_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = root / "runtime.sqlite"
            store = StateStore(database, [root])
            store.create_contract(
                "p1", root_contract={"objective": "pipe-client"}, acceptance_contract={"required": []}
            )
            lease = store.acquire_daemon_lease("p1", "pipe-client-daemon")
            store.close()

            authkey = "scorp-client-test-authkey"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[2])
            env["SCORP_TEST_PIPE_AUTHKEY"] = authkey
            python = r"C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe"
            process = subprocess.Popen(
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
            response = None
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        response = RuntimePipeClient(
                            project_id="p1", authkey=authkey.encode("utf-8"), timeout_seconds=2
                        ).request(
                            {
                                "protocol_version": "scorp.runtime.command/1",
                                "request_id": "pipe-client-1",
                                "command": "runtime.status",
                                "project_id": "p1",
                                "actor": "gpt-master",
                                "payload": {},
                            }
                        )
                        break
                    except OSError:
                        if process.poll() is not None:
                            break
                        time.sleep(0.05)
            finally:
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=10)
                    self.fail(f"pipe CLI did not exit: stdout={stdout!r} stderr={stderr!r}")
            self.assertIsNotNone(response, stderr)
            self.assertEqual("OK", response["status"])
            self.assertEqual("p1", response["project_id"])
            self.assertEqual(0, process.returncode, stderr)

    def test_client_does_not_retry_or_accept_a_different_project(self):
        client = RuntimePipeClient(project_id="p1", authkey=b"scorp-client-test-authkey")
        with self.assertRaisesRegex(ValueError, "PROJECT_SCOPE_MISMATCH"):
            client.request(
                {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "pipe-client-scope-1",
                    "command": "runtime.status",
                    "project_id": "p2",
                    "actor": "gpt-master",
                    "payload": {},
                }
            )


if __name__ == "__main__":
    unittest.main()
