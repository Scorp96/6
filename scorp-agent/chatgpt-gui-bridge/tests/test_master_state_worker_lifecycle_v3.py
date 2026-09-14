import datetime as dt
import json
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from project_state_v3 import ProjectStateStore
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc


def worker(worker_id, n):
    return {
        "worker_id": worker_id,
        "session_id": f"{worker_id}-session-{n}",
        "objective_sha256": f"{n:064x}",
    }


class MasterStateWorkerLifecycleV3Tests(unittest.TestCase):
    def _state(self, active_workers):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 8,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "WORKERS",
            "next_exact_action": "integrate worker results",
            "active_master_window": None,
            "active_workers": active_workers,
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

    def _master_turn(self, payload=None):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "master-turn-8",
            "project_id": "p",
            "state_version": 8,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-8",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": payload or {"event": "CONTINUE_CORE"},
        }

    def _worker_turn(self, row):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-finished-turn",
            "project_id": "p",
            "state_version": 8,
            "actor_kind": "WORKER",
            "actor_id": row["worker_id"],
            "session_id": row["session_id"],
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": row["objective_sha256"],
            "payload": {"event": "ASSIGNMENT"},
        }

    def _make(self, td, active_workers):
        root = pathlib.Path(td)
        state_path = root / "state.json"
        state_path.write_text(json.dumps(self._state(active_workers)), encoding="utf-8")
        store = ProjectStateStore(state_path)
        store.load()
        leases = MasterWindowLeaseStore(root / "lease.json")
        queue = WorkerEventQueueV3(root / "worker-events")
        now = dt.datetime(2026, 9, 11, 10, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-8", "master-turn-8", 8, now=now, ttl_seconds=3600)
        tx = MasterStateTransitionV3(store, leases, queue)
        return store, leases, queue, tx, now

    def _event_master_turn(self, queue, finished_worker):
        event_id = queue.enqueue(
            self._worker_turn(finished_worker),
            {"kind": "HANDOFF", "evidence": ["done"]},
        )["event_id"]
        return self._master_turn({
            "event": "WORKER_HANDOFF",
            "worker_event_id": event_id,
            "worker_id": finished_worker["worker_id"],
        }), event_id

    def test_consumed_worker_event_removes_only_finished_worker_and_acks_event(self):
        with tempfile.TemporaryDirectory() as td:
            finished = worker("worker-finished", 1)
            survivor = worker("worker-survivor", 2)
            store, _, queue, tx, now = self._make(td, [finished, survivor])
            turn, event_id = self._event_master_turn(queue, finished)

            result = tx.apply(turn, {
                "kind": "CONTINUE",
                "state_patch": {"next_exact_action": "use worker evidence"},
            }, now=now + dt.timedelta(minutes=1))

            state = store.load()
            self.assertEqual(9, state["state_version"])
            self.assertEqual([survivor], state["active_workers"])
            event = queue.get(event_id)
            self.assertEqual("ACKED", event["state"])
            self.assertEqual(result["transition_id"], event["ack_transition_id"])

    def test_ack_crash_replay_keeps_worker_removed_without_second_state_version(self):
        with tempfile.TemporaryDirectory() as td:
            finished = worker("worker-finished", 1)
            survivor = worker("worker-survivor", 2)
            store, _, queue, tx, now = self._make(td, [finished, survivor])
            turn, event_id = self._event_master_turn(queue, finished)
            response = {
                "kind": "CONTINUE",
                "state_patch": {"next_exact_action": "repair worker event ack"},
            }
            real_ack = queue.acknowledge

            def fail_ack(*args, **kwargs):
                raise RuntimeError("INJECTED_ACK_CRASH")

            queue.acknowledge = fail_ack
            with self.assertRaisesRegex(RuntimeError, "INJECTED_ACK_CRASH"):
                tx.apply(turn, response, now=now + dt.timedelta(minutes=1))

            self.assertEqual(9, store.load()["state_version"])
            self.assertEqual([survivor], store.load()["active_workers"])
            self.assertEqual("PENDING", queue.get(event_id)["state"])

            queue.acknowledge = real_ack
            replay = tx.apply(turn, response, now=now + dt.timedelta(minutes=2))
            self.assertTrue(replay["replayed"])
            self.assertEqual(9, store.load()["state_version"])
            self.assertEqual([survivor], store.load()["active_workers"])
            self.assertEqual("ACKED", queue.get(event_id)["state"])

    def test_dispatch_appends_new_workers_without_replacing_existing_pool(self):
        with tempfile.TemporaryDirectory() as td:
            existing = worker("worker-existing", 1)
            new_one = worker("worker-new", 2)
            store, _, _, tx, now = self._make(td, [existing])

            result = tx.apply(self._master_turn(), {
                "kind": "DISPATCH",
                "state_patch": {"next_exact_action": "continue while both workers run"},
            }, allocated_workers=[new_one], now=now + dt.timedelta(minutes=1))

            self.assertEqual(9, result["state"]["state_version"])
            self.assertEqual([existing, new_one], result["state"]["active_workers"])

    def test_worker_event_and_dispatch_remove_finished_then_append_new_workers_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            finished = worker("worker-finished", 1)
            survivor = worker("worker-survivor", 2)
            newcomer = worker("worker-new", 3)
            store, _, queue, tx, now = self._make(td, [finished, survivor])
            turn, event_id = self._event_master_turn(queue, finished)

            result = tx.apply(turn, {
                "kind": "DISPATCH",
                "state_patch": {"next_exact_action": "continue with survivor and newcomer"},
            }, allocated_workers=[newcomer], now=now + dt.timedelta(minutes=1))

            self.assertEqual([survivor, newcomer], result["state"]["active_workers"])
            self.assertEqual("ACKED", queue.get(event_id)["state"])

    def test_dispatch_rejects_total_active_pool_above_limit_before_state_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            existing = [worker(f"worker-{i}", i + 1) for i in range(7)]
            new_workers = [worker("worker-new-a", 20), worker("worker-new-b", 21)]
            store, _, _, tx, now = self._make(td, existing)

            with self.assertRaisesRegex(ValueError, "MASTER_ACTIVE_WORKERS_LIMIT"):
                tx.apply(self._master_turn(), {
                    "kind": "DISPATCH",
                    "state_patch": {"next_exact_action": "too many"},
                }, allocated_workers=new_workers, now=now + dt.timedelta(minutes=1))

            self.assertEqual(8, store.load()["state_version"])
            self.assertEqual(existing, store.load()["active_workers"])


if __name__ == "__main__":
    unittest.main()
