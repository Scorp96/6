import pathlib
import tempfile
import unittest

from worker_event_queue_v3 import WorkerEventQueueV3


class WorkerEventQueueV3Tests(unittest.TestCase):
    def _turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-turn-001",
            "project_id": "project-1",
            "state_version": 7,
            "actor_kind": "WORKER",
            "actor_id": "worker-abc123",
            "session_id": "worker-abc123-session-1",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {
                "event": "ASSIGNMENT",
                "controller_session_id": "a-window-7",
            },
        }

    def test_worker_handoff_is_durable_and_not_bound_to_old_master_session(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkerEventQueueV3(pathlib.Path(td) / "worker-events")
            result = queue.enqueue(self._turn(), {"kind": "HANDOFF", "evidence": ["ok"]})
            self.assertEqual("QUEUED", result["status"])
            event = queue.get(result["event_id"])
            self.assertEqual("PENDING", event["state"])
            self.assertEqual("project-1", event["project_id"])
            self.assertEqual("worker-abc123", event["worker_id"])
            self.assertEqual("HANDOFF", event["response_kind"])
            self.assertNotIn("controller_session_id", event)
            self.assertNotIn("master_session_id", event)

    def test_exact_replay_is_idempotent_and_changed_response_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkerEventQueueV3(pathlib.Path(td) / "worker-events")
            turn = self._turn()
            response = {"kind": "BLOCKER", "blocker": "needs evidence"}
            first = queue.enqueue(turn, response)
            second = queue.enqueue(turn, response)
            self.assertEqual(first["event_id"], second["event_id"])
            self.assertEqual("ALREADY_QUEUED", second["status"])
            with self.assertRaisesRegex(ValueError, "WORKER_EVENT_SOURCE_CONFLICT"):
                queue.enqueue(turn, {"kind": "HANDOFF", "evidence": ["different"]})

    def test_pending_events_survive_restart_and_are_project_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "worker-events"
            first = WorkerEventQueueV3(root)
            event_id = first.enqueue(self._turn(), {"kind": "HANDOFF"})["event_id"]
            other = self._turn()
            other["turn_id"] = "worker-turn-002"
            other["project_id"] = "project-2"
            other["actor_id"] = "worker-def456"
            other["session_id"] = "worker-def456-session-1"
            first.enqueue(other, {"kind": "BLOCKER"})

            restarted = WorkerEventQueueV3(root)
            pending = restarted.pending("project-1")
            self.assertEqual([event_id], [x["event_id"] for x in pending])
            self.assertEqual("PENDING", restarted.get(event_id)["state"])

    def test_acknowledge_is_idempotent_and_preserves_event_payload(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkerEventQueueV3(pathlib.Path(td) / "worker-events")
            event_id = queue.enqueue(self._turn(), {"kind": "HANDOFF", "evidence": ["ok"]})["event_id"]
            first = queue.acknowledge(event_id, "master-tx-123")
            second = queue.acknowledge(event_id, "master-tx-123")
            self.assertEqual("ACKED", first["state"])
            self.assertEqual(first, second)
            self.assertEqual(["ok"], first["worker_response"]["evidence"])
            self.assertEqual([], queue.pending("project-1"))
            with self.assertRaisesRegex(ValueError, "WORKER_EVENT_ACK_CONFLICT"):
                queue.acknowledge(event_id, "master-tx-different")

    def test_only_worker_handoff_or_blocker_can_enter_queue(self):
        with tempfile.TemporaryDirectory() as td:
            queue = WorkerEventQueueV3(pathlib.Path(td) / "worker-events")
            master = self._turn()
            master["actor_kind"] = "MASTER"
            master["actor_id"] = "A"
            with self.assertRaisesRegex(ValueError, "WORKER_EVENT_WORKER_ONLY"):
                queue.enqueue(master, {"kind": "HANDOFF"})
            with self.assertRaisesRegex(ValueError, "WORKER_EVENT_RESPONSE_KIND_INVALID"):
                queue.enqueue(self._turn(), {"kind": "CONTINUE"})


if __name__ == "__main__":
    unittest.main()
