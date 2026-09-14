import datetime as dt
import json
import pathlib
import tempfile
import unittest

from master_window_lease_v3 import MasterWindowLeaseStore
from project_state_v3 import ProjectStateStore
from worker_event_pump_v3 import WorkerEventPumpV3
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc


class MasterWorkerFusionV3Tests(unittest.TestCase):
    def _assignment(self):
        return {
            "objective_sha256": "1" * 64,
            "task": "inspect and edit bounded file",
            "resource_scope": ["file:a.py"],
            "success_criteria": ["targeted test passes"],
        }

    def _state(self):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 4,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "REVIEW_WORKER",
            "next_exact_action": "A reviews worker evidence",
            "active_master_window": None,
            "active_workers": [{
                "worker_id": "worker-001",
                "session_id": "worker-001-session",
                "objective_sha256": "1" * 64,
                "assignment": self._assignment(),
            }],
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
            "state_version": 3,
            "actor_kind": "WORKER",
            "actor_id": "worker-001",
            "session_id": "worker-001-session",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {"event": "ASSIGNMENT", "assignment": self._assignment()},
        }

    def _make(self, td, worker_response):
        root = pathlib.Path(td)
        state_path = root / "project-state.json"
        state_path.write_text(json.dumps(self._state()), encoding="utf-8")
        state = ProjectStateStore(state_path)
        before = state.load()
        leases = MasterWindowLeaseStore(root / "master-lease.json")
        now = dt.datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-current", "resume-current", 4, now=now, ttl_seconds=1500)
        queue = WorkerEventQueueV3(root / "worker-events")
        event_id = queue.enqueue(self._worker_turn(), worker_response)["event_id"]
        pump = WorkerEventPumpV3(state, leases, queue, root / "master-worker-v3-outbox")
        return root, state, before, queue, event_id, pump, now

    def test_aligned_worker_evidence_is_delivered_to_a_with_guard_without_state_mutation(self):
        response = {
            "kind": "HANDOFF",
            "evidence": ["test passed"],
            "resource_scope": ["file:a.py"],
        }
        with tempfile.TemporaryDirectory() as td:
            root, state, before, queue, event_id, pump, now = self._make(td, response)
            result = pump.run_once("p", now=now + dt.timedelta(seconds=1))
            self.assertEqual("QUEUED", result["status"])
            self.assertEqual(before, state.load())
            self.assertEqual("PENDING", queue.get(event_id)["state"])
            turn = json.loads(
                (root / "master-worker-v3-outbox" / f"{result['turn_id']}.json").read_text(encoding="utf-8-sig")
            )
            payload = turn["payload"]
            self.assertEqual("WORKER_HANDOFF", payload["event"])
            self.assertEqual(self._assignment(), payload["worker_assignment"])
            self.assertEqual("ALIGNED", payload["worker_guard"]["verdict"])
            self.assertFalse(payload["worker_guard"]["authoritative"])
            self.assertEqual(["test passed"], payload["worker_response"]["evidence"])

    def test_scope_violation_is_delivered_as_untrusted_evidence_for_a_to_reject(self):
        response = {
            "kind": "HANDOFF",
            "evidence": ["claimed edit"],
            "resource_scope": ["file:forbidden.py"],
        }
        with tempfile.TemporaryDirectory() as td:
            root, state, before, queue, event_id, pump, now = self._make(td, response)
            result = pump.run_once("p", now=now + dt.timedelta(seconds=1))
            self.assertEqual(before, state.load())
            self.assertEqual("PENDING", queue.get(event_id)["state"])
            turn = json.loads(
                (root / "master-worker-v3-outbox" / f"{result['turn_id']}.json").read_text(encoding="utf-8-sig")
            )
            guard = turn["payload"]["worker_guard"]
            self.assertEqual("SCOPE_VIOLATION", guard["verdict"])
            self.assertFalse(guard["authoritative"])
            self.assertEqual(["file:forbidden.py"], guard["out_of_scope"])


if __name__ == "__main__":
    unittest.main()
