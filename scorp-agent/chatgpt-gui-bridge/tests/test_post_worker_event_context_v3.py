import datetime as dt
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from project_state_v3 import ProjectStateStore
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc


class PostWorkerEventContextV3Tests(unittest.TestCase):
    def _make(self, td):
        root = pathlib.Path(td)
        store = ProjectStateStore(root / "project-state.json")
        store.create(
            {
                "protocol_version": "scorp.project-state/v1",
                "project_id": "p",
                "master_identity": "A",
                "state_version": 0,
                "goal_contract_sha256": "g" * 64,
                "acceptance_sha256": "a" * 64,
                "status": "ACTIVE",
                "current_phase": "WORKER_ACTIVE",
                "next_exact_action": "wait",
                "active_master_window": None,
                "active_workers": [
                    {
                        "worker_id": "worker-one",
                        "session_id": "worker-one-session",
                        "objective_sha256": "1" * 64,
                    }
                ],
                "completed": [],
                "blocked": [],
                "evidence": [],
                "commits": [],
                "tests": [],
            }
        )
        leases = MasterWindowLeaseStore(root / "master-window-lease.json")
        now = dt.datetime(2026, 9, 13, 2, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-0", "resume-a-0", 0, now=now, ttl_seconds=1500)
        queue = WorkerEventQueueV3(root / "worker-events-v3")
        worker_turn = {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-one-turn",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "WORKER",
            "actor_id": "worker-one",
            "session_id": "worker-one-session",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {"event": "ASSIGNMENT", "assignment": {"objective_sha256": "1" * 64}},
        }
        event_id = queue.enqueue(
            worker_turn,
            {"kind": "BLOCKER", "reason": "ACTOR_GUI_TIMEOUT", "source_turn_id": worker_turn["turn_id"]},
        )["event_id"]
        tx = MasterStateTransitionV3(store, leases, queue)
        event_turn = {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-event-master-0",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-0",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {
                "event": "WORKER_BLOCKER",
                "worker_event_id": event_id,
                "source_worker_turn_id": worker_turn["turn_id"],
                "worker_id": "worker-one",
            },
        }
        event_result = tx.apply(event_turn, {"kind": "WAIT"}, now=now + dt.timedelta(seconds=1))
        return store, leases, queue, tx, event_id, event_result, now

    def _followup_turn(self, state_version, event_id):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": f"post-event-{state_version}",
            "project_id": "p",
            "state_version": state_version,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-0",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {
                "event": "CONTINUE_CORE_AFTER_WORKER_EVENT",
                "worker_event_id": event_id,
                "source_worker_turn_id": "worker-one-turn",
            },
        }

    def test_immediate_post_event_followup_treats_acked_event_as_context_only(self):
        with tempfile.TemporaryDirectory() as td:
            store, leases, queue, tx, event_id, event_result, now = self._make(td)
            event_before = queue.get(event_id)
            self.assertEqual("ACKED", event_before["state"])
            self.assertEqual(event_result["transition_id"], event_before["ack_transition_id"])
            self.assertEqual(event_result["transition_id"], store.load()["last_master_transition_id"])

            result = tx.apply(
                self._followup_turn(1, event_id),
                {"kind": "TERMINAL", "terminal_status": "HARD_BLOCKED"},
                now=now + dt.timedelta(seconds=2),
            )
            self.assertEqual("HARD_BLOCKED", result["state"]["status"])
            self.assertEqual(2, result["state"]["state_version"])
            self.assertEqual([], result["state"]["active_workers"])
            event_after = queue.get(event_id)
            self.assertEqual("ACKED", event_after["state"])
            self.assertEqual(event_result["transition_id"], event_after["ack_transition_id"])
            self.assertEqual("RELEASED", leases.load("p")["status"])

    def test_stale_post_event_followup_cannot_reuse_old_acked_event_as_context(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, _, tx, event_id, event_result, now = self._make(td)
            continue_turn = {
                "protocol_version": "scorp.master-worker/turn-v3",
                "turn_id": "master-continue-after-event",
                "project_id": "p",
                "state_version": 1,
                "actor_kind": "MASTER",
                "actor_id": "A",
                "session_id": "a-window-0",
                "root_objective_sha256": "g" * 64,
                "acceptance_sha256": "a" * 64,
                "payload": {"event": "CONTINUE_CORE"},
            }
            continued = tx.apply(continue_turn, {"kind": "CONTINUE"}, now=now + dt.timedelta(seconds=2))
            self.assertEqual(2, continued["state"]["state_version"])
            self.assertNotEqual(event_result["transition_id"], store.load()["last_master_transition_id"])
            with self.assertRaisesRegex(ValueError, "WORKER_EVENT_ACK_CONFLICT"):
                tx.apply(
                    self._followup_turn(2, event_id),
                    {"kind": "TERMINAL", "terminal_status": "HARD_BLOCKED"},
                    now=now + dt.timedelta(seconds=3),
                )


if __name__ == "__main__":
    unittest.main()
