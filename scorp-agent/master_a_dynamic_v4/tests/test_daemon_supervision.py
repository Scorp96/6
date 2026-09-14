from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError


class DaemonSupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "daemon"}, acceptance_contract={"required": []}
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_supervision_state_is_durable_and_failure_backoff_is_bounded(self):
        initial = self.store.get_daemon_supervision("p1")
        self.assertEqual("CLOSED", initial["circuit_state"])
        start = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
        first = self.store.record_daemon_failure(
            "p1", reason="CRASH", now=start, restart_budget=2, base_backoff_seconds=2
        )
        self.assertEqual(1, first["restart_count"])
        self.assertEqual(1, first["consecutive_failures"])
        self.assertEqual("BACKOFF", first["circuit_state"])
        self.assertEqual("2026-09-15T00:00:02Z", first["backoff_until"])

        blocked = self.store.record_daemon_failure(
            "p1", reason="CRASH_AGAIN", now=start + dt.timedelta(seconds=3), restart_budget=2, base_backoff_seconds=2
        )
        self.assertEqual(2, blocked["restart_count"])
        self.assertEqual("BLOCKED", blocked["circuit_state"])
        self.assertEqual("RESTART_BUDGET_EXHAUSTED", blocked["block_reason"])
        self.assertEqual("BLOCKED", self.store.daemon_recovery_gate("p1", now=start + dt.timedelta(seconds=4))["status"])

        self.store.record_daemon_recovery("p1", now=start + dt.timedelta(seconds=5))
        recovered = self.store.get_daemon_supervision("p1")
        self.assertEqual(1, recovered["recovery_count"])
        self.assertEqual("CLOSED", recovered["circuit_state"])
        self.assertEqual(0, recovered["consecutive_failures"])
        # Resetting the active crash-loop counter must not reuse a historical
        # event primary key on the next failure sequence.
        second_sequence = self.store.record_daemon_failure(
            "p1", reason="CRASH_NEW_SEQUENCE", now=start + dt.timedelta(seconds=6), restart_budget=2
        )
        self.assertEqual(1, second_sequence["restart_count"])

    def test_expired_lease_records_a_restart_without_double_authority(self):
        start = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
        first = self.store.acquire_daemon_lease("p1", "daemon-a", now=start, ttl_seconds=2)
        self.assertEqual(1, first["daemon_epoch"])
        second = self.store.acquire_daemon_lease(
            "p1", "daemon-b", now=start + dt.timedelta(seconds=3), ttl_seconds=2
        )
        self.assertEqual(2, second["daemon_epoch"])
        supervision = self.store.get_daemon_supervision("p1")
        self.assertEqual(1, supervision["restart_count"])
        with self.assertRaisesRegex(StoreInvariantError, "DAEMON_LEASE_ACTIVE"):
            self.store.acquire_daemon_lease(
                "p1", "daemon-c", now=start + dt.timedelta(seconds=3, milliseconds=1), ttl_seconds=2
            )


if __name__ == "__main__":
    unittest.main()
