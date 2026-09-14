from __future__ import annotations

import datetime as dt
import pathlib
import tempfile
import unittest


UTC = dt.timezone.utc


class MasterWatchdogTests(unittest.TestCase):
    def make_store(self, root: pathlib.Path):
        from master_a_dynamic_v4.state_store import StateStore

        store = StateStore(root / "state.sqlite3", allowed_roots=[root])
        store.create_contract(
            "project-watchdog",
            root_contract={"objective": "watch master"},
            acceptance_contract={"required": ["AC08"]},
        )
        return store

    def test_session_heartbeat_and_stale_session_are_fenced(self):
        from master_a_dynamic_v4.master_watchdog import MasterWatchdog
        from master_a_dynamic_v4.state_store import StoreInvariantError

        start = dt.datetime(2026, 9, 14, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = self.make_store(root)
            try:
                watchdog = MasterWatchdog(store, "project-watchdog", ttl_seconds=30)
                first = watchdog.start("master-1", now=start)
                self.assertEqual("MASTER_ACTIVE", watchdog.run_once(now=start + dt.timedelta(seconds=10))["status"])
                watchdog.heartbeat("master-1", master_epoch=first["master_epoch"], now=start + dt.timedelta(seconds=20))
                stale = watchdog.run_once(now=start + dt.timedelta(seconds=51))
                self.assertEqual("RESUME_REQUIRED", stale["status"])
                self.assertEqual("master-1", stale["stale_session_id"])
                resumed = watchdog.start("master-2", now=start + dt.timedelta(seconds=52))
                self.assertEqual(first["master_epoch"] + 1, resumed["master_epoch"])
                with self.assertRaisesRegex(StoreInvariantError, "MASTER_SESSION_FENCED"):
                    watchdog.heartbeat("master-1", master_epoch=first["master_epoch"], now=start + dt.timedelta(seconds=53))
            finally:
                store.close()

    def test_terminal_project_never_requests_resume(self):
        from master_a_dynamic_v4.master_watchdog import MasterWatchdog

        start = dt.datetime(2026, 9, 14, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = self.make_store(root)
            try:
                with store._transaction() as conn:
                    conn.execute("UPDATE project_state SET status='COMPLETE' WHERE project_id=?", ("project-watchdog",))
                watchdog = MasterWatchdog(store, "project-watchdog", ttl_seconds=30)
                self.assertEqual("TERMINAL", watchdog.run_once(now=start)["status"])
            finally:
                store.close()

    def test_explicit_end_requires_current_epoch_and_allows_reactivation(self):
        from master_a_dynamic_v4.master_watchdog import MasterWatchdog

        start = dt.datetime(2026, 9, 14, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = self.make_store(root)
            try:
                watchdog = MasterWatchdog(store, "project-watchdog", ttl_seconds=30)
                first = watchdog.start("master-1", now=start)
                ended = watchdog.end("master-1", master_epoch=first["master_epoch"], reason="MONITOR_STOP", now=start + dt.timedelta(seconds=1))
                self.assertEqual("ENDED", ended["state"])
                resumed = watchdog.start("master-2", now=start + dt.timedelta(seconds=2))
                self.assertEqual(first["master_epoch"] + 1, resumed["master_epoch"])
            finally:
                store.close()

    def test_external_epoch_advance_fences_active_session_and_does_not_deadlock_resume(self):
        from master_a_dynamic_v4.master_watchdog import MasterWatchdog

        start = dt.datetime(2026, 9, 14, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = self.make_store(root)
            try:
                watchdog = MasterWatchdog(store, "project-watchdog", ttl_seconds=30)
                first = watchdog.start("master-1", now=start)
                self.assertEqual(1, store.advance_master_epoch("project-watchdog", expected_epoch=first["master_epoch"]))
                decision = watchdog.run_once(now=start + dt.timedelta(seconds=1))
                self.assertEqual("RESUME_REQUIRED", decision["status"])
                replacement = watchdog.start("master-2", now=start + dt.timedelta(seconds=2))
                self.assertEqual(2, replacement["master_epoch"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
