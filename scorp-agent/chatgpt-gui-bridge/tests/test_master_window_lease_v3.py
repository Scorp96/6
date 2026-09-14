import datetime as dt
import pathlib
import tempfile
import unittest

from master_window_lease_v3 import MasterWindowLeaseStore

UTC = dt.timezone.utc


class MasterWindowLeaseV3Tests(unittest.TestCase):
    def test_acquire_and_restart_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "master-lease.json"
            store = MasterWindowLeaseStore(path)
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            lease = store.acquire("p", "a-window-7", "turn-7", 7, now=now, ttl_seconds=1500)
            self.assertEqual("ACTIVE", lease["status"])
            self.assertEqual("a-window-7", lease["session_id"])
            self.assertEqual(7, lease["project_state_version_at_start"])
            self.assertEqual(lease, MasterWindowLeaseStore(path).load("p"))

    def test_second_live_master_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            store = MasterWindowLeaseStore(pathlib.Path(td) / "master-lease.json")
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            store.acquire("p", "a-window-7", "turn-7", 7, now=now, ttl_seconds=1500)
            with self.assertRaisesRegex(ValueError, "MASTER_WINDOW_ALREADY_ACTIVE"):
                store.acquire("p", "a-window-8", "turn-8", 8, now=now + dt.timedelta(minutes=1), ttl_seconds=1500)

    def test_expired_lease_allows_new_master_session(self):
        with tempfile.TemporaryDirectory() as td:
            store = MasterWindowLeaseStore(pathlib.Path(td) / "master-lease.json")
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            store.acquire("p", "a-window-7", "turn-7", 7, now=now, ttl_seconds=60)
            lease = store.acquire("p", "a-window-8", "turn-8", 8, now=now + dt.timedelta(seconds=61), ttl_seconds=1500)
            self.assertEqual("a-window-8", lease["session_id"])
            self.assertEqual("ACTIVE", lease["status"])

    def test_same_session_heartbeat_extends_lease(self):
        with tempfile.TemporaryDirectory() as td:
            store = MasterWindowLeaseStore(pathlib.Path(td) / "master-lease.json")
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            first = store.acquire("p", "a-window-7", "turn-7", 7, now=now, ttl_seconds=60)
            second = store.heartbeat("p", "a-window-7", now=now + dt.timedelta(seconds=30), ttl_seconds=1500)
            self.assertGreater(second["lease_until"], first["lease_until"])
            with self.assertRaisesRegex(ValueError, "MASTER_WINDOW_OWNER_MISMATCH"):
                store.heartbeat("p", "a-window-other", now=now + dt.timedelta(seconds=40), ttl_seconds=1500)

    def test_drain_releases_master_without_deleting_audit_identity(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "master-lease.json"
            store = MasterWindowLeaseStore(path)
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            store.acquire("p", "a-window-7", "turn-7", 7, now=now, ttl_seconds=1500)
            drained = store.drain("p", "a-window-7", reason="WINDOW_DRAIN", now=now + dt.timedelta(minutes=20))
            self.assertEqual("DRAINED", drained["status"])
            self.assertEqual("WINDOW_DRAIN", drained["drain_reason"])
            self.assertEqual("a-window-7", drained["session_id"])
            self.assertFalse(store.is_active("p", now=now + dt.timedelta(minutes=20)))

    def test_terminal_release_is_not_active(self):
        with tempfile.TemporaryDirectory() as td:
            store = MasterWindowLeaseStore(pathlib.Path(td) / "master-lease.json")
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            store.acquire("p", "a-window-7", "turn-7", 7, now=now, ttl_seconds=1500)
            released = store.release("p", "a-window-7", reason="COMPLETE", now=now + dt.timedelta(minutes=3))
            self.assertEqual("RELEASED", released["status"])
            self.assertFalse(store.is_active("p", now=now + dt.timedelta(minutes=3)))


if __name__ == "__main__":
    unittest.main()
