from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

from master_a_dynamic_v4.runtime_pipe_client import RuntimePipeClient
from master_a_dynamic_v4.runtime_pipe_cli import authkey_from_file
from master_a_dynamic_v4.state_store import StateStore


class R2RuntimePipeLifecycleTests(unittest.TestCase):
    def test_authkey_file_reads_local_bytes_without_environment(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "runtime-pipe.key"
            expected = b"scorp-r2-runtime-pipe-authkey-32"
            path.write_bytes(expected)
            self.assertEqual(expected, authkey_from_file(path))

    def test_installer_passes_only_pipe_secret_path_not_secret_value(self):
        installer = (
            pathlib.Path(__file__).resolve().parents[1]
            / "install-v4-daemon.ps1"
        )
        text = installer.read_text(encoding="utf-8")
        self.assertIn("$RuntimePipeAuthKeyFile", text)
        self.assertIn("--runtime-pipe-authkey-file", text)
        self.assertNotIn("SCORP_RUNTIME_PIPE_AUTHKEY=", text)

    @unittest.skipUnless(os.name == "nt", "Windows Named Pipe runtime")
    def test_daemon_hosts_runtime_pipe_under_its_current_epoch(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = root / "state.sqlite3"
            secret = root / "runtime-pipe.key"
            health = root / "health.json"
            project = "r2-pipe-" + str(os.getpid())
            actor = "gpt-master"
            authkey = b"scorp-r2-daemon-hosted-pipe-key"
            secret.write_bytes(authkey)

            store = StateStore(database, [root])
            store.create_contract(
                project,
                root_contract={"objective": "daemon hosted pipe"},
                acceptance_contract={"required": []},
            )
            store.close()

            bridge_root = pathlib.Path(__file__).resolve().parents[1]
            agent_root = bridge_root.parent
            script = bridge_root / "tools" / "v4_daemon_runtime.py"
            python = sys.executable
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join(
                [str(agent_root), str(bridge_root)]
            )
            process = subprocess.Popen(
                [
                    python,
                    "-B",
                    str(script),
                    "--database-path",
                    str(database),
                    "--allowed-root",
                    str(root),
                    "--project-id",
                    project,
                    "--health-path",
                    str(health),
                    "--interval-seconds",
                    "0.1",
                    "--daemon-ttl-seconds",
                    "10",
                    "--forever",
                    "--runtime-pipe-authkey-file",
                    str(secret),
                    "--runtime-pipe-actor",
                    actor,
                ],
                cwd=str(bridge_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            response = None
            error = None
            try:
                client = RuntimePipeClient(
                    project_id=project,
                    authkey=authkey,
                    timeout_seconds=2,
                )
                request = {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "r2-daemon-pipe-status",
                    "command": "runtime.status",
                    "project_id": project,
                    "actor": actor,
                    "payload": {},
                }
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        response = client.request(request)
                        break
                    except (OSError, RuntimeError):
                        time.sleep(0.1)
                if response is None:
                    error = "PIPE_NOT_READY"
            finally:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=10)

            self.assertIsNone(
                error,
                f"{error}: returncode={process.returncode} "
                f"stdout={stdout!r} stderr={stderr!r}",
            )
            self.assertEqual("OK", response["status"])
            self.assertEqual(project, response["project_id"])
            self.assertGreaterEqual(
                int(response["result"]["daemon"]["daemon_epoch"]),
                1,
            )


if __name__ == "__main__":
    unittest.main()
