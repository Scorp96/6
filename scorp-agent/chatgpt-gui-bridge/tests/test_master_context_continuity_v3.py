import datetime as dt
import hashlib
import json
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_relay_v3 import MasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3
from worker_event_pump_v3 import WorkerEventPumpV3
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc


def _sha(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _state(project_id="p"):
    return {
        "protocol_version": "scorp.project-state/v1",
        "project_id": project_id,
        "master_identity": "A",
        "state_version": 0,
        "goal_contract_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "status": "ACTIVE",
        "current_phase": "BOOT",
        "next_exact_action": "continue project",
        "active_master_window": None,
        "active_workers": [],
        "completed": [],
        "blocked": [],
        "evidence": [],
        "commits": [],
        "tests": [],
    }


class MasterContextContinuityV3Tests(unittest.TestCase):
    def test_dispatch_master_core_carries_exact_committed_project_state_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            state = ProjectStateStore(root / "project-state.json")
            state.create(_state())
            committed = state.update(0, {
                "current_phase": "PARALLEL",
                "next_exact_action": "continue core while workers execute",
                "active_workers": [{
                    "worker_id": "worker-planned",
                    "session_id": "worker-planned-session",
                    "objective_sha256": "1" * 64,
                }],
            })
            leases = MasterWindowLeaseStore(root / "master-lease.json")
            sessions = SessionRegistryV3(root / "sessions.json")
            transition = MasterStateTransitionV3(state, leases)
            relay = MasterWorkerRelayV3(root, sessions, gui_turn=None, master_transition=transition)
            parent = {
                "protocol_version": "scorp.master-worker/turn-v3",
                "turn_id": "master-parent",
                "project_id": "p",
                "state_version": 0,
                "actor_kind": "MASTER",
                "actor_id": "A",
                "session_id": "a-window-0",
                "root_objective_sha256": "g" * 64,
                "acceptance_sha256": "a" * 64,
                "payload": {"event": "RESUME_MASTER", "project_state": _state()},
            }
            response = {
                "kind": "DISPATCH",
                "assignments": [{"objective_sha256": "1" * 64, "task": "bounded worker"}],
                "state_patch": {"next_exact_action": "continue core while workers execute"},
            }
            children = relay._route(parent, response, committed_state_version=1)
            master_children = []
            for turn_id in children:
                turn = json.loads((root / "master-worker-v3-outbox" / f"{turn_id}.json").read_text(encoding="utf-8"))
                if turn["actor_kind"] == "MASTER":
                    master_children.append(turn)
            self.assertEqual(1, len(master_children))
            payload = master_children[0]["payload"]
            self.assertEqual(1, master_children[0]["state_version"])
            self.assertEqual(committed, payload["project_state"])
            self.assertEqual(_sha(committed), payload["project_state_sha256"])

    def test_worker_event_master_turn_carries_current_project_state_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            state = ProjectStateStore(root / "project-state.json")
            initial = _state()
            initial["active_workers"] = [{
                "worker_id": "worker-one",
                "session_id": "worker-one-session",
                "objective_sha256": "2" * 64,
            }]
            state.create(initial)
            leases = MasterWindowLeaseStore(root / "master-lease.json")
            now = dt.datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
            leases.acquire("p", "a-window-0", "master-0", 0, now=now, ttl_seconds=1500)
            queue = WorkerEventQueueV3(root / "worker-events-v3")
            worker_turn = {
                "protocol_version": "scorp.master-worker/turn-v3",
                "turn_id": "worker-turn-1",
                "project_id": "p",
                "state_version": 0,
                "actor_kind": "WORKER",
                "actor_id": "worker-one",
                "session_id": "worker-one-session",
                "root_objective_sha256": "g" * 64,
                "acceptance_sha256": "a" * 64,
                "objective_sha256": "2" * 64,
                "payload": {"event": "ASSIGNMENT"},
            }
            queue.enqueue(worker_turn, {"kind": "HANDOFF", "evidence": ["done"]})
            outbox = root / "master-worker-v3-outbox"
            pump = WorkerEventPumpV3(state, leases, queue, outbox)
            result = pump.run_once("p", now=now + dt.timedelta(seconds=1))
            turn = json.loads((outbox / f"{result['turn_id']}.json").read_text(encoding="utf-8"))
            current = state.load()
            self.assertEqual(current["state_version"], turn["state_version"])
            self.assertEqual(current, turn["payload"]["project_state"])
            self.assertEqual(_sha(current), turn["payload"]["project_state_sha256"])


if __name__ == "__main__":
    unittest.main()
