from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class V4DaemonStopRequestTests(unittest.TestCase):
    def test_stop_path_may_use_authority_control_directory_but_cannot_escape_it(self):
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            authority = Path(td) / "active"
            workspace = authority / "workspace"
            workspace.mkdir(parents=True)
            database = authority / "state.sqlite3"
            database.write_bytes(b"")
            control_path = authority / "control" / "runtime.stop.json"

            self.assertEqual(
                control_path.resolve(),
                runtime._resolve_stop_request_path(control_path, workspace, database),
            )
            with self.assertRaisesRegex(RuntimeError, "STOP_REQUEST_PATH_OUTSIDE_AUTHORITY_ROOT"):
                runtime._resolve_stop_request_path(
                    Path(td) / "outside.stop.json", workspace, database
                )

    def test_runtime_passes_fenced_stop_signal_and_acknowledges_after_cleanup(self):
        from master_a_dynamic_v4.state_store import StateStore
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            health = root / "health.json"
            stop_path = root / "runtime.stop.json"
            with StateStore(db, [root]) as store:
                store.create_contract(
                    "p", root_contract={"objective": "x"}, acceptance_contract={"ids": []}
                )
            stop_path.write_text(
                json.dumps(
                    {
                        "project_id": "p",
                        "daemon_owner": "owner-1",
                        "daemon_epoch": 1,
                        "pid": __import__("os").getpid(),
                        "request_id": "stop-live-1",
                    }
                ),
                encoding="utf-8",
            )

            class FakeDaemon:
                def __init__(self, *_args, **kwargs):
                    self.health_path = Path(kwargs["health_path"])

                def run_loop(self, **kwargs):
                    self.assert_stop(kwargs["stop_event"])
                    self.health_path.write_text(
                        json.dumps(
                            {
                                "status": "HEALTHY",
                                "scheduling_state": "RUNNING",
                                "dispatch_allowed": False,
                            }
                        ),
                        encoding="utf-8",
                    )
                    return []

                @staticmethod
                def assert_stop(stop_event):
                    if not stop_event.is_set():
                        raise AssertionError("matching stop event was not connected")

            args = runtime.build_parser().parse_args(
                [
                    "--database-path", str(db),
                    "--allowed-root", str(root),
                    "--project-id", "p",
                    "--actor-id", "owner-1",
                    "--health-path", str(health),
                    "--stop-request-path", str(stop_path),
                    "--max-iterations", "1",
                ]
            )
            with mock.patch.object(runtime, "LocalDaemon", FakeDaemon), \
                    mock.patch("builtins.print"):
                self.assertEqual(0, runtime.run_runtime(args))

            ack = json.loads(stop_path.with_suffix(".ack.json").read_text(encoding="utf-8"))
            self.assertEqual("stop-live-1", ack["request_id"])
            with StateStore(db, [root]) as reopened:
                lease = reopened.runtime_snapshot("p", daemon_epoch=1)["daemon"]
                self.assertEqual("RELEASED", lease["lease_status"])

    def test_matching_request_stops_once_and_writes_acknowledgement(self):
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            request_path = root / "runtime.stop.json"
            request_path.write_text(
                json.dumps(
                    {
                        "project_id": "p",
                        "daemon_owner": "owner-1",
                        "daemon_epoch": 4,
                        "pid": 123,
                        "request_id": "stop-1",
                    }
                ),
                encoding="utf-8",
            )
            stop = runtime.FencedStopRequest(
                request_path,
                project_id="p",
                daemon_owner="owner-1",
                daemon_epoch=4,
                pid=123,
            )

            self.assertTrue(stop.is_set())
            self.assertEqual("stop-1", stop.request_id)
            ack_path = stop.acknowledge()
            ack = json.loads(ack_path.read_text(encoding="utf-8"))
            self.assertEqual("ACKNOWLEDGED", ack["status"])
            self.assertEqual("stop-1", ack["request_id"])
            self.assertEqual(4, ack["daemon_epoch"])

    def test_stale_identity_is_ignored_and_cannot_stop_new_daemon(self):
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            request_path = root / "runtime.stop.json"
            request_path.write_text(
                json.dumps(
                    {
                        "project_id": "p",
                        "daemon_owner": "owner-old",
                        "daemon_epoch": 3,
                        "pid": 122,
                        "request_id": "stop-old",
                    }
                ),
                encoding="utf-8",
            )
            stop = runtime.FencedStopRequest(
                request_path,
                project_id="p",
                daemon_owner="owner-new",
                daemon_epoch=4,
                pid=123,
            )

            self.assertFalse(stop.is_set())
            self.assertEqual("STOP_REQUEST_IDENTITY_MISMATCH", stop.rejection_reason)
            self.assertIsNone(stop.request_id)
            self.assertFalse(request_path.with_suffix(".ack.json").exists())
            rejection = json.loads(
                request_path.with_suffix(".rejected.json").read_text(encoding="utf-8")
            )
            self.assertEqual("REJECTED", rejection["status"])
            self.assertEqual("STOP_REQUEST_IDENTITY_MISMATCH", rejection["reason"])
            self.assertEqual(4, rejection["expected_daemon_epoch"])


if __name__ == "__main__":
    unittest.main()
