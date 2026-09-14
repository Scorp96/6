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


class MasterStateWorkerEventAckV3Tests(unittest.TestCase):
    def _state(self):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 8,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "PHASE_2",
            "next_exact_action": "integrate worker event",
            "active_master_window": None,
            "active_workers": [],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

    def _worker_turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-turn-001",
            "project_id": "p",
            "state_version": 7,
            "actor_kind": "WORKER",
            "actor_id": "worker-abc123",
            "session_id": "worker-abc123-session-1",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {"event": "ASSIGNMENT"},
        }

    def _make(self, td):
        root = pathlib.Path(td)
        state_path = root / "state.json"
        state_path.write_text(json.dumps(self._state()), encoding="utf-8")
        store = ProjectStateStore(state_path)
        store.load()
        leases = MasterWindowLeaseStore(root / "lease.json")
        queue = WorkerEventQueueV3(root / "worker-events")
        event_id = queue.enqueue(self._worker_turn(), {"kind": "HANDOFF", "evidence": ["ok"]})["event_id"]
        now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-8", "event-turn-8", 8, now=now, ttl_seconds=1500)
        turn = {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "event-turn-8",
            "project_id": "p",
            "state_version": 8,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-8",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {
                "event": "WORKER_HANDOFF",
                "worker_event_id": event_id,
                "worker_id": "worker-abc123",
            },
        }
        tx = MasterStateTransitionV3(store, leases, queue)
        return store, leases, queue, tx, turn, event_id, now

    def test_successful_state_transition_acknowledges_worker_event_with_transition_id(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, queue, tx, turn, event_id, now = self._make(td)
            result = tx.apply(turn, {
                "kind": "CONTINUE",
                "state_patch": {"next_exact_action": "continue after worker evidence"},
            }, now=now + dt.timedelta(minutes=1))
            self.assertEqual(9, store.load()["state_version"])
            event = queue.get(event_id)
            self.assertEqual("ACKED", event["state"])
            self.assertEqual(result["transition_id"], event["ack_transition_id"])

    def test_ack_failure_after_state_commit_is_repaired_by_exact_replay_without_new_version(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, queue, tx, turn, event_id, now = self._make(td)
            real_ack = queue.acknowledge

            def fail_ack(*args, **kwargs):
                raise RuntimeError("SIMULATED_ACK_CRASH")

            queue.acknowledge = fail_ack
            response = {"kind": "CONTINUE", "state_patch": {"next_exact_action": "resume ack repair"}}
            with self.assertRaisesRegex(RuntimeError, "SIMULATED_ACK_CRASH"):
                tx.apply(turn, response, now=now + dt.timedelta(minutes=1))
            self.assertEqual(9, store.load()["state_version"])
            self.assertEqual("PENDING", queue.get(event_id)["state"])

            queue.acknowledge = real_ack
            replay = tx.apply(turn, response, now=now + dt.timedelta(minutes=2))
            self.assertTrue(replay["replayed"])
            self.assertEqual(9, store.load()["state_version"])
            self.assertEqual(replay["transition_id"], queue.get(event_id)["ack_transition_id"])

    def test_missing_worker_event_fails_before_project_state_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, _, tx, turn, _, now = self._make(td)
            turn["payload"]["worker_event_id"] = "worker-event-missing"
            with self.assertRaisesRegex(ValueError, "WORKER_EVENT_NOT_FOUND"):
                tx.apply(turn, {"kind": "CONTINUE", "state_patch": {"next_exact_action": "bad"}}, now=now)
            self.assertEqual(8, store.load()["state_version"])

    def test_worker_event_project_or_contract_mismatch_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, queue, tx, turn, event_id, now = self._make(td)
            path = queue.events_dir / f"{event_id}.json"
            event = json.loads(path.read_text(encoding="utf-8"))
            event["root_objective_sha256"] = "x" * 64
            path.write_text(json.dumps(event), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "MASTER_WORKER_EVENT_GOAL_MISMATCH"):
                tx.apply(turn, {"kind": "CONTINUE", "state_patch": {"next_exact_action": "bad"}}, now=now)
            self.assertEqual(8, store.load()["state_version"])


if __name__ == "__main__":
    unittest.main()
