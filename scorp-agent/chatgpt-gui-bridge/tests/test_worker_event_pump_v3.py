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


class WorkerEventPumpV3Tests(unittest.TestCase):
    def _assignment(self):
        return {
            "objective_sha256": "1" * 64,
            "task": "worker bounded task",
            "resource_scope": ["file:a.py"],
        }

    def _state(self, version=8, status="ACTIVE"):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "project-1",
            "master_identity": "A",
            "state_version": version,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": status,
            "current_phase": "PHASE_2",
            "next_exact_action": "continue integration",
            "active_master_window": {"stale": "ignored"},
            "active_workers": [{
                "worker_id": "worker-abc123",
                "session_id": "worker-abc123-session-1",
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
                "controller_session_id": "dead-a-window-7",
                "assignment": self._assignment(),
            },
        }

    def _make(self, td, *, state_version=8, status="ACTIVE", with_live_master=True):
        root = pathlib.Path(td)
        state_path = root / "project-state.json"
        state_path.write_text(json.dumps(self._state(state_version, status)), encoding="utf-8")
        state = ProjectStateStore(state_path)
        state.load()
        leases = MasterWindowLeaseStore(root / "master-lease.json")
        queue = WorkerEventQueueV3(root / "worker-events")
        event_id = queue.enqueue(self._worker_turn(), {"kind": "HANDOFF", "evidence": ["worker-ok"]})["event_id"]
        now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
        if with_live_master:
            leases.acquire("project-1", "a-window-8-current", "resume-a-8", state_version, now=now, ttl_seconds=1500)
        pump = WorkerEventPumpV3(state, leases, queue, root / "master-worker-v3-outbox")
        return root, state, leases, queue, pump, event_id, now

    def test_live_master_receives_event_on_current_session_and_current_state_version(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, _, queue, pump, event_id, now = self._make(td)
            result = pump.run_once("project-1", now=now + dt.timedelta(minutes=1))
            self.assertEqual("QUEUED", result["status"])
            turn_path = root / "master-worker-v3-outbox" / f"{result['turn_id']}.json"
            turn = json.loads(turn_path.read_text(encoding="utf-8"))
            self.assertEqual("scorp.master-worker/turn-v3", turn["protocol_version"])
            self.assertEqual("MASTER", turn["actor_kind"])
            self.assertEqual("A", turn["actor_id"])
            self.assertEqual("a-window-8-current", turn["session_id"])
            self.assertEqual(8, turn["state_version"])
            self.assertEqual("WORKER_HANDOFF", turn["payload"]["event"])
            self.assertEqual(event_id, turn["payload"]["worker_event_id"])
            self.assertEqual("worker-abc123", turn["payload"]["worker_id"])
            self.assertEqual(["worker-ok"], turn["payload"]["worker_response"]["evidence"])
            self.assertNotEqual("dead-a-window-7", turn["session_id"])
            self.assertEqual("PENDING", queue.get(event_id)["state"])

    def test_no_live_master_leaves_event_pending_for_d_to_resume_a(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, _, queue, pump, event_id, now = self._make(td, with_live_master=False)
            result = pump.run_once("project-1", now=now)
            self.assertEqual("MASTER_INACTIVE", result["status"])
            self.assertEqual("PENDING", queue.get(event_id)["state"])
            self.assertEqual([], list((root / "master-worker-v3-outbox").glob("*.json")))

    def test_terminal_project_does_not_deliver_worker_event(self):
        for status in ("COMPLETE", "HARD_BLOCKED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root, _, _, queue, pump, event_id, now = self._make(td, status=status)
                result = pump.run_once("project-1", now=now)
                self.assertEqual(status, result["status"])
                self.assertEqual("PENDING", queue.get(event_id)["state"])
                self.assertEqual([], list((root / "master-worker-v3-outbox").glob("*.json")))

    def test_exact_rerun_is_idempotent_and_does_not_duplicate_turn_file(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, _, queue, pump, event_id, now = self._make(td)
            first = pump.run_once("project-1", now=now + dt.timedelta(minutes=1))
            second = pump.run_once("project-1", now=now + dt.timedelta(minutes=2))
            self.assertEqual(first["turn_id"], second["turn_id"])
            self.assertEqual("ALREADY_QUEUED", second["status"])
            self.assertEqual(1, len(list((root / "master-worker-v3-outbox").glob("*.json"))))
            self.assertEqual("PENDING", queue.get(event_id)["state"])

    def test_delivery_identity_changes_when_authoritative_state_version_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root, state, _, _, pump, _, now = self._make(td)
            first = pump.run_once("project-1", now=now + dt.timedelta(minutes=1))
            state.update(8, {"next_exact_action": "new authoritative action"})
            second = pump.run_once("project-1", now=now + dt.timedelta(minutes=2))
            self.assertNotEqual(first["turn_id"], second["turn_id"])
            second_turn = json.loads((root / "master-worker-v3-outbox" / f"{second['turn_id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(9, second_turn["state_version"])


if __name__ == "__main__":
    unittest.main()
