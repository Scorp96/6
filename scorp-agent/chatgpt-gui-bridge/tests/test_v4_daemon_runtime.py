from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
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
            store.acquire_daemon_lease("p", "holder", ttl_seconds=60)
            store.close()
            common = [
                "--database-path", str(db), "--allowed-root", str(root),
                "--project-id", "p", "--daemon-epoch", "1", "--max-iterations", "1",
            ]
            first = runtime.build_parser().parse_args(common + ["--actor-id", "daemon-a", "--health-path", str(root / "a.json")])
            with self.assertRaisesRegex(StoreInvariantError, "DAEMON_LEASE_ACTIVE"):
                with contextlib.redirect_stdout(io.StringIO()):
                    runtime.run_runtime(first)
            second = runtime.build_parser().parse_args(common + ["--actor-id", "daemon-b", "--health-path", str(root / "b.json")])
            with self.assertRaisesRegex(StoreInvariantError, "DAEMON_LEASE_ACTIVE"):
                with contextlib.redirect_stdout(io.StringIO()):
                    runtime.run_runtime(second)

    def test_one_shot_runtime_releases_its_lease_for_a_later_owner(self):
        from master_a_dynamic_v4.state_store import StateStore
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            store = StateStore(db, [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.close()
            common = [
                "--database-path", str(db), "--allowed-root", str(root),
                "--project-id", "p", "--max-iterations", "1",
            ]
            first = runtime.build_parser().parse_args(common + ["--actor-id", "daemon-a", "--health-path", str(root / "a.json")])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2, runtime.run_runtime(first))
            second = runtime.build_parser().parse_args(common + ["--actor-id", "daemon-b", "--health-path", str(root / "b.json")])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, runtime.run_runtime(second))
            self.assertEqual(2, json.loads(output.getvalue())["daemon_epoch"])
            with StateStore(db, [root]) as reopened:
                self.assertEqual(0, reopened.get_daemon_supervision("p")["restart_count"])

    def test_runtime_can_acquire_current_epoch_without_static_epoch_argument(self):
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
                    "--database-path", str(db), "--allowed-root", str(root),
                    "--project-id", "p", "--actor-id", "daemon-a",
                    "--health-path", str(root / "health.json"), "--max-iterations", "1",
                ]
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = runtime.run_runtime(args)
            self.assertEqual(2, rc)
            summary = json.loads(output.getvalue())
            self.assertEqual(1, summary["daemon_epoch"])

    def test_epoch_mismatch_releases_startup_lease_before_any_action(self):
        from master_a_dynamic_v4.state_store import StateStore
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            store = StateStore(db, [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.close()
            bad = runtime.build_parser().parse_args(
                [
                    "--database-path", str(db), "--allowed-root", str(root),
                    "--project-id", "p", "--actor-id", "daemon-a",
                    "--daemon-epoch", "99", "--daemon-ttl-seconds", "60",
                    "--health-path", str(root / "bad.json"), "--max-iterations", "1",
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "DAEMON_EPOCH_MISMATCH"):
                with contextlib.redirect_stdout(io.StringIO()):
                    runtime.run_runtime(bad)
            with StateStore(db, [root]) as reopened:
                lease = reopened.runtime_snapshot("p", daemon_epoch=1)["daemon"]
                self.assertEqual("RELEASED", lease["lease_status"])
                self.assertEqual(0, reopened.get_daemon_supervision("p")["restart_count"])
            good = runtime.build_parser().parse_args(
                [
                    "--database-path", str(db), "--allowed-root", str(root),
                    "--project-id", "p", "--actor-id", "daemon-b",
                    "--health-path", str(root / "good.json"), "--max-iterations", "1",
                ]
            )
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(2, runtime.run_runtime(good))
            self.assertEqual(2, json.loads(output.getvalue())["daemon_epoch"])

    def test_runtime_reacquires_a_new_epoch_after_the_previous_lease_expires(self):
        from master_a_dynamic_v4.state_store import StateStore
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            store = StateStore(db, [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.close()
            common = [
                "--database-path", str(db), "--allowed-root", str(root),
                "--project-id", "p", "--actor-id", "daemon-a",
                "--daemon-ttl-seconds", "1", "--max-iterations", "1",
            ]
            first = runtime.build_parser().parse_args(common + ["--health-path", str(root / "first.json")])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2, runtime.run_runtime(first))
            time.sleep(1.2)
            second = runtime.build_parser().parse_args(common + ["--health-path", str(root / "second.json")])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, runtime.run_runtime(second))
            self.assertEqual(2, json.loads(output.getvalue())["daemon_epoch"])

    def test_successful_runtime_iteration_resets_prior_crash_sequence_before_exit(self):
        import datetime as dt

        from master_a_dynamic_v4.state_store import StateStore
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            store = StateStore(db, [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.start_master_session("p", "master-a", ttl_seconds=60)
            store.record_daemon_failure(
                "p",
                reason="OLD_CRASH",
                now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
                restart_budget=3,
            )
            before = store.get_daemon_supervision("p")
            self.assertEqual(1, before["restart_count"])
            store.close()
            args = runtime.build_parser().parse_args(
                [
                    "--database-path", str(db), "--allowed-root", str(root),
                    "--project-id", "p", "--actor-id", "daemon-a",
                    "--health-path", str(root / "health.json"), "--max-iterations", "2",
                    "--supervise-master", "--master-session-id", "master-a",
                    "--interval-seconds", "0",
                ]
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, runtime.run_runtime(args))
            with StateStore(db, [root]) as reopened:
                after = reopened.get_daemon_supervision("p")
                self.assertEqual(0, after["restart_count"])
                self.assertEqual(0, after["consecutive_failures"])
                self.assertEqual("CLOSED", after["circuit_state"])
                self.assertEqual(1, after["recovery_count"])
                with reopened._connection() as conn:
                    events = conn.execute(
                        "SELECT COUNT(*) FROM events WHERE project_id=? AND kind='DAEMON_RECOVERY'",
                        ("p",),
                    ).fetchone()[0]
                self.assertEqual(1, events)

    def test_runtime_can_attach_the_existing_master_supervisor_without_enabling_browser_send(self):
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
                    "--database-path", str(db), "--allowed-root", str(root),
                    "--project-id", "p", "--daemon-epoch", "1", "--actor-id", "daemon-a",
                    "--health-path", str(root / "health.json"), "--max-iterations", "1",
                    "--supervise-master", "--master-session-id", "master-a",
                ]
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = runtime.run_runtime(args)
            self.assertEqual(2, rc)
            summary = json.loads(output.getvalue())
            self.assertTrue(summary["master_supervision"])
            self.assertEqual("FORBIDDEN", summary["browser_send"])

    def test_supervised_runtime_renews_an_active_master_session(self):
        from master_a_dynamic_v4.state_store import StateStore
        from tools import v4_daemon_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "state.sqlite3"
            store = StateStore(db, [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            started = store.start_master_session("p", "master-a", ttl_seconds=60)
            before = started["heartbeat_at"]
            store.close()
            args = runtime.build_parser().parse_args(
                [
                    "--database-path", str(db), "--allowed-root", str(root),
                    "--project-id", "p", "--daemon-epoch", "1", "--actor-id", "daemon-a",
                    "--health-path", str(root / "health.json"), "--max-iterations", "1",
                    "--supervise-master", "--master-session-id", "master-a",
                ]
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = runtime.run_runtime(args)
            self.assertEqual(0, rc)
            self.assertEqual("HEARTBEAT_IDLE", json.loads(output.getvalue())["decisions"][0]["action"])
            with StateStore(db, [root]) as reopened:
                with reopened._connection() as conn:
                    after = conn.execute(
                        "SELECT heartbeat_at FROM master_sessions WHERE project_id=? AND session_id=?",
                        ("p", "master-a"),
                    ).fetchone()[0]
                self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
