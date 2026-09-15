from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
from multiprocessing.connection import Client
from unittest.mock import patch

from master_a_dynamic_v4.runtime_pipe import pipe_name
from master_a_dynamic_v4.runtime_pipe_cli import _parser, authkey_from_env
from master_a_dynamic_v4.state_store import StateStore


class RuntimePipeCliTests(unittest.TestCase):
    def test_daemon_epoch_is_optional_for_restart_epoch_reacquisition(self):
        args = _parser().parse_args(
            [
                "--database", "C:/runtime.sqlite",
                "--project-id", "p1",
                "--allowed-root", "C:/runtime",
                "--once",
            ]
        )
        self.assertIsNone(args.daemon_epoch)

    def test_authkey_from_env_requires_a_nontrivial_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "PIPE_AUTHKEY_ENV_MISSING"):
                authkey_from_env("SCORP_TEST_PIPE_AUTHKEY")
        with patch.dict(os.environ, {"SCORP_TEST_PIPE_AUTHKEY": "too-short"}, clear=True):
            with self.assertRaisesRegex(ValueError, "PIPE_AUTHKEY_TOO_SHORT"):
                authkey_from_env("SCORP_TEST_PIPE_AUTHKEY")

    @unittest.skipUnless(os.name == "nt", "Windows Named Pipe transport")
    def test_once_process_serves_one_authenticated_runtime_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = root / "runtime.sqlite"
            store = StateStore(database, [root])
            store.create_contract(
                "p1", root_contract={"objective": "pipe-cli"}, acceptance_contract={"required": []}
            )
            store.acquire_daemon_lease("p1", "pipe-cli-daemon")
            store.close()

            authkey = "scorp-cli-test-authkey"
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
            endpoint = pipe_name("p1")
            client = None
            try:
                deadline = time.monotonic() + 10
                while client is None and time.monotonic() < deadline:
                    try:
                        client = Client(endpoint, family="AF_PIPE", authkey=authkey.encode("utf-8"))
                    except (FileNotFoundError, ConnectionRefusedError, OSError):
                        if process.poll() is not None:
                            break
                        time.sleep(0.05)
                self.assertIsNotNone(client, process.stderr.read() if process.poll() is not None else "pipe unavailable")
                request = {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "pipe-cli-1",
                    "command": "runtime.status",
                    "project_id": "p1",
                    "actor": "gpt-master",
                    "payload": {},
                }
                client.send_bytes(json.dumps(request, separators=(",", ":")).encode("utf-8"))
                response = json.loads(client.recv_bytes().decode("utf-8"))
            finally:
                if client is not None:
                    client.close()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=10)
                    self.fail(f"pipe CLI did not exit: stdout={stdout!r} stderr={stderr!r}")
            self.assertEqual("OK", response["status"], stderr)
            self.assertEqual("p1", response["project_id"])
            self.assertEqual(0, process.returncode, stderr)


if __name__ == "__main__":
    unittest.main()
