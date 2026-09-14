from __future__ import annotations

import unittest
import tempfile
import datetime as dt
from pathlib import Path

from master_a_dynamic_v4.activation_arbiter import ActivationArbiter, ArbiterSnapshot
from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError


class ActivationArbiterTests(unittest.TestCase):
    def setUp(self):
        self.arbiter = ActivationArbiter(actor_id="daemon-test")

    def test_terminal_has_highest_priority_and_no_activation(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p",
                project_status="COMPLETE",
                master_epoch=4,
                daemon_epoch=8,
                master_active=True,
                active_workers=0,
                free_slots=2,
                ready_tasks=1,
                ambiguous_intents=1,
            )
        )
        self.assertEqual("TERMINAL", decision.action)
        self.assertEqual("PROJECT_TERMINAL", decision.reason)
        self.assertEqual("daemon-test", decision.actor_id)
        self.assertTrue(decision.decision_id)

    def test_ambiguous_browser_effect_precedes_master_resume(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p",
                project_status="ACTIVE",
                master_epoch=4,
                daemon_epoch=8,
                master_active=False,
                active_workers=1,
                free_slots=1,
                ready_tasks=2,
                ambiguous_intents=1,
            )
        )
        self.assertEqual("RECONCILE_AMBIGUOUS", decision.action)
        self.assertEqual("AMBIGUOUS_BROWSER_SIDE_EFFECT", decision.reason)

    def test_master_resume_precedes_worker_assignment(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p",
                project_status="ACTIVE",
                master_epoch=4,
                daemon_epoch=8,
                master_active=False,
                active_workers=0,
                free_slots=2,
                ready_tasks=2,
                ambiguous_intents=0,
            )
        )
        self.assertEqual("RESUME_MASTER", decision.action)
        self.assertEqual("MASTER_LEASE_MISSING", decision.reason)

    def test_ready_work_is_assigned_only_to_free_capacity(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p",
                project_status="ACTIVE",
                master_epoch=4,
                daemon_epoch=8,
                master_active=True,
                active_workers=1,
                free_slots=1,
                ready_tasks=2,
                ambiguous_intents=0,
            )
        )
        self.assertEqual("ASSIGN_WORKER", decision.action)
        self.assertEqual("READY_TASKS_AND_FREE_SLOT", decision.reason)
        self.assertEqual(1, decision.capacity)

    def test_idle_is_distinct_from_liveness_failure(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p",
                project_status="ACTIVE",
                master_epoch=4,
                daemon_epoch=8,
                master_active=True,
                active_workers=0,
                free_slots=2,
                ready_tasks=0,
                ambiguous_intents=0,
                progress_state="ACTIVE_NO_VISIBLE_PROGRESS",
            )
        )
        self.assertEqual("HEARTBEAT_IDLE", decision.action)
        self.assertEqual("NO_READY_WORK", decision.reason)

    def test_decision_can_be_persisted_idempotently_in_the_transaction_store(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.sqlite3", [td])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            decision = self.arbiter.decide(
                ArbiterSnapshot("p", "ACTIVE", 0, 1, True, 0, 1, 0, 0)
            )
            first = store.record_activation_decision(decision.as_dict())
            second = store.record_activation_decision(decision.as_dict())
            self.assertEqual(first, second)
            self.assertEqual(1, store.count_activation_decisions("p"))

            altered = dict(decision.as_dict())
            altered["reason"] = "CONFLICT"
            with self.assertRaises(StoreInvariantError):
                store.record_activation_decision(altered)

    def test_store_builds_a_daemon_snapshot_without_mutating_browser_state(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.sqlite3", [td])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            snapshot = store.activation_snapshot("p", daemon_epoch=7)
            self.assertEqual("ACTIVE", snapshot.project_status)
            self.assertFalse(snapshot.master_active)
            self.assertEqual(2, snapshot.free_slots)
            self.assertEqual(0, snapshot.ambiguous_intents)

    def test_daemon_lease_fences_a_second_owner_and_advances_epoch_after_expiry(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.sqlite3", [td])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
            first = store.acquire_daemon_lease("p", "daemon-a", now=start, ttl_seconds=10)
            self.assertEqual(1, first["daemon_epoch"])
            with self.assertRaises(StoreInvariantError):
                store.acquire_daemon_lease("p", "daemon-b", now=start + dt.timedelta(seconds=1), ttl_seconds=10)
            renewed = store.heartbeat_daemon_lease("p", "daemon-a", daemon_epoch=1, now=start + dt.timedelta(seconds=2), ttl_seconds=10)
            self.assertEqual("daemon-a", renewed["owner_id"])
            second = store.acquire_daemon_lease("p", "daemon-b", now=start + dt.timedelta(seconds=20), ttl_seconds=10)
            self.assertEqual(2, second["daemon_epoch"])
            with self.assertRaises(StoreInvariantError):
                store.heartbeat_daemon_lease("p", "daemon-a", daemon_epoch=1, now=start + dt.timedelta(seconds=21), ttl_seconds=10)


if __name__ == "__main__":
    unittest.main()
