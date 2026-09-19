from __future__ import annotations

import unittest
import tempfile
import datetime as dt
import time
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

    def test_local_daemon_runs_completion_before_terminal_snapshot(self):
        from master_a_dynamic_v4.daemon import LocalDaemon

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            order = []
            def complete():
                order.append("complete")
                with store._transaction() as conn:
                    conn.execute("update project_state set status='COMPLETE',phase='COMPLETE' where project_id='p'")
            def snapshot():
                order.append("snapshot")
                return store.activation_snapshot("p", daemon_epoch=1)
            daemon = LocalDaemon(
                store, project_id="p", daemon_epoch=1, snapshot_provider=snapshot,
                project_completion=complete, health_path=root / "health.json",
            )
            decision = daemon.run_once()
            self.assertEqual(["complete", "snapshot"], order)
            self.assertEqual("TERMINAL", decision.action)
            store.close()

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

    def test_confirmed_stall_recovery_precedes_new_assignment(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p", project_status="ACTIVE", master_epoch=1, daemon_epoch=2,
                master_active=True, active_workers=0, free_slots=2, ready_tasks=1,
                ambiguous_intents=0, progress_state="STALLED_CONFIRMED",
            )
        )
        self.assertEqual("RECOVER_STALLED", decision.action)

    def test_operator_fence_and_pending_result_wakeup_precede_assignment(self):
        paused = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p", project_status="ACTIVE", master_epoch=1, daemon_epoch=2,
                master_active=True, active_workers=0, free_slots=2, ready_tasks=1,
                ambiguous_intents=0, operator_state="PAUSED",
            )
        )
        self.assertEqual("BLOCKED", paused.action)
        self.assertEqual("OPERATOR_FENCE_PAUSED", paused.reason)
        wake = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p", project_status="ACTIVE", master_epoch=1, daemon_epoch=2,
                master_active=True, active_workers=0, free_slots=2, ready_tasks=1,
                ambiguous_intents=0, pending_results=1,
            )
        )
        self.assertEqual("WAKE_MASTER", wake.action)

    def test_emergency_stop_has_explicit_priority_before_ambiguous_reconcile(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p", project_status="ACTIVE", master_epoch=1, daemon_epoch=2,
                master_active=True, active_workers=1, free_slots=1, ready_tasks=1,
                ambiguous_intents=1, operator_state="EMERGENCY_STOPPED",
            )
        )
        self.assertEqual("EMERGENCY_STOP", decision.action)
        self.assertEqual("OPERATOR_EMERGENCY_STOPPED", decision.reason)

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

    def test_store_marks_an_active_assignment_without_a_live_lease_as_lost_worker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worktree = root / "worktree"
            worktree.mkdir()
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            store.start_master_session("p", "master-a", ttl_seconds=300)
            from master_a_dynamic_v4.path_policy import PathPolicy
            from master_a_dynamic_v4.scheduler import Scheduler

            scheduler = Scheduler(store, "p", PathPolicy([root]), max_workers=2)
            scheduler.enqueue_graph([
                {"task_id": "T1", "objective_sha256": "a" * 64, "resource_scope": [worktree / "a.txt"], "dependencies": []}
            ])
            claim = scheduler.claim_runnable(master_epoch=0, limit=1)[0]
            with store._transaction() as conn:
                conn.execute(
                    "UPDATE leases SET expires_at=? WHERE assignment_id=?",
                    ("2000-01-01T00:00:00Z", claim.assignment_id),
                )
            snapshot = store.activation_snapshot("p", daemon_epoch=7)
            self.assertTrue(snapshot.active_worker_lost)
            decision = self.arbiter.decide(snapshot)
            self.assertEqual("RESUME_WORKER", decision.action)

    def test_missing_operator_control_is_unknown_and_blocks_arbiter(self):
        with tempfile.TemporaryDirectory() as td:
            store = StateStore(Path(td) / "state.sqlite3", [td])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            with store._transaction() as conn:
                conn.execute("DELETE FROM operator_controls WHERE project_id=?", ("p",))
            snapshot = store.activation_snapshot("p", daemon_epoch=7)
            self.assertEqual("UNKNOWN", snapshot.operator_state)
            decision = self.arbiter.decide(snapshot)
            self.assertEqual("BLOCKED", decision.action)
            self.assertEqual("OPERATOR_FENCE_UNKNOWN", decision.reason)
            store.close()

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


    def test_captured_worker_response_is_pending_master_work_and_wakes_daemon(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worktree = root / "worktree"
            worktree.mkdir()
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            master = store.start_master_session("p", "master-a", ttl_seconds=300)
            from master_a_dynamic_v4.path_policy import PathPolicy
            from master_a_dynamic_v4.scheduler import Scheduler
            scheduler = Scheduler(store, "p", PathPolicy([root]), max_workers=2)
            scheduler.enqueue_graph([{"task_id":"T1","objective_sha256":"a"*64,"resource_scope":[worktree/"a.txt"],"dependencies":[]}])
            claim = scheduler.claim_runnable(master_epoch=int(master["master_epoch"]), limit=1)[0]
            intent_id = f"worker-intent-{claim.assignment_id}"
            store.prepare_intent("p", intent_id, actor_id=claim.worker_id, channel=f"worker/{claim.slot_id}", action_kind="CHATGPT_WORKER_SUBMIT", payload={"prompt":"x"})
            with store._transaction() as conn:
                conn.execute("UPDATE action_intents SET state='BLOCKED_AMBIGUOUS' WHERE intent_id=?", (intent_id,))
                conn.execute("UPDATE outbox SET state='BLOCKED' WHERE intent_id=?", (intent_id,))
            store.capture_response(intent_id, response={"work_result_version":"1"}, conversation_url="https://chatgpt.com/c/test", remote_identity="remote", observation={"source":"test"})
            store.finalize_intent(intent_id)
            snapshot = store.activation_snapshot("p", daemon_epoch=1)
            self.assertEqual(1, snapshot.pending_results)
            decision = self.arbiter.decide(snapshot)
            self.assertEqual("WAKE_MASTER", decision.action)
            self.assertEqual("PENDING_RESULT_REQUIRES_MASTER_WAKE", decision.reason)
            store.close()

    def test_daemon_keeps_lease_alive_during_long_action(self):
        from master_a_dynamic_v4.daemon import LocalDaemon

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract(
                "p",
                root_contract={"objective": "x"},
                acceptance_contract={"required": []},
            )
            heartbeats = []

            def beat():
                heartbeats.append(time.monotonic())

            def handle(_decision):
                time.sleep(0.06)
                return {"status": "IDLE"}

            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=1,
                snapshot_provider=lambda: ArbiterSnapshot(
                    project_id="p",
                    project_status="ACTIVE",
                    master_epoch=0,
                    daemon_epoch=1,
                    master_active=True,
                    active_workers=0,
                    free_slots=1,
                    ready_tasks=1,
                    ambiguous_intents=0,
                ),
                action_handlers={"ASSIGN_WORKER": handle},
                lease_heartbeat=beat,
                action_heartbeat_interval_seconds=0.01,
                health_path=root / "health.json",
            )
            decision = daemon.run_once()
            self.assertEqual("ASSIGN_WORKER", decision.action)
            self.assertGreaterEqual(len(heartbeats), 3)
            store.close()

    def test_idle_durable_change_can_activate_master_reasoning(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p", project_status="ACTIVE", master_epoch=1, daemon_epoch=2,
                master_active=True, active_workers=0, free_slots=2, ready_tasks=0,
                ambiguous_intents=0, reasoning_required=True,
            )
        )
        self.assertEqual("REASON_MASTER", decision.action)
        self.assertEqual("DURABLE_STATE_REQUIRES_MASTER_REASONING", decision.reason)

    def test_ready_worker_work_precedes_master_reasoning(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p", project_status="ACTIVE", master_epoch=1, daemon_epoch=2,
                master_active=True, active_workers=0, free_slots=2, ready_tasks=1,
                ambiguous_intents=0, reasoning_required=True,
            )
        )
        self.assertEqual("ASSIGN_WORKER", decision.action)

    def test_active_worker_suppresses_new_master_reasoning(self):
        decision = self.arbiter.decide(
            ArbiterSnapshot(
                project_id="p", project_status="ACTIVE", master_epoch=1, daemon_epoch=2,
                master_active=True, active_workers=1, free_slots=1, ready_tasks=0,
                ambiguous_intents=0, reasoning_required=True,
            )
        )
        self.assertEqual("HEARTBEAT_IDLE", decision.action)


if __name__ == "__main__":
    unittest.main()
