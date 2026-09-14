import asyncio
import datetime as dt
import pathlib
import tempfile
import unittest

from continuation_watchdog_v3 import ContinuationWatchdog
from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_coordinator_v3 import MasterWorkerCoordinatorV3
from master_worker_relay_v3 import MasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3
from worker_event_pump_v3 import WorkerEventPumpV3
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc


class ScriptedGui:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def __call__(self, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.calls.append({
            "turn_id": turn_id,
            "actor_kind": actor_kind,
            "conversation_url": conversation_url,
        })
        if not self.rows:
            raise AssertionError("UNEXPECTED_GUI_CALL")
        response, url = self.rows.pop(0)
        return response, "snapshot", url


def _initial_state():
    return {
        "protocol_version": "scorp.project-state/v1",
        "project_id": "project-coordinator",
        "master_identity": "A",
        "state_version": 0,
        "goal_contract_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "status": "ACTIVE",
        "current_phase": "BOOT",
        "next_exact_action": "start autonomous master",
        "active_master_window": None,
        "active_workers": [],
        "completed": [],
        "blocked": [],
        "evidence": [],
        "commits": [],
        "tests": [],
    }


class MasterWorkerCoordinatorV3Tests(unittest.TestCase):
    def _make(self, td):
        root = pathlib.Path(td)
        state = ProjectStateStore(root / "project-state.json")
        state.create(_initial_state())
        leases = MasterWindowLeaseStore(root / "master-window-lease.json")
        queue = WorkerEventQueueV3(root / "worker-events-v3")
        sessions = SessionRegistryV3(root / "sessions-v3.json")
        outbox = root / "master-worker-v3-outbox"
        watchdog = ContinuationWatchdog(state, leases, outbox)
        pump = WorkerEventPumpV3(state, leases, queue, outbox)
        transition = MasterStateTransitionV3(state, leases, queue)
        gui = ScriptedGui([
            ({
                "kind": "CONTINUE",
                "state_patch": {"current_phase": "CORE", "next_exact_action": "continue in A1"},
            }, "https://chatgpt.com/c/a1-coordinator"),
            ({
                "kind": "DRAIN",
                "state_patch": {"current_phase": "HANDOFF", "next_exact_action": "resume in A2"},
            }, "https://chatgpt.com/c/a1-coordinator"),
            ({"kind": "WAIT"}, "https://chatgpt.com/c/a1-coordinator"),
        ])
        relay = MasterWorkerRelayV3(root, sessions, gui, master_transition=transition)
        coordinator = MasterWorkerCoordinatorV3(
            "project-coordinator",
            state,
            leases,
            watchdog,
            pump,
            relay,
            outbox,
            master_ttl_seconds=1500,
        )
        return root, state, leases, sessions, gui, coordinator

    def test_autonomous_master_reactivates_same_a_conversation_without_user_activation(self):
        with tempfile.TemporaryDirectory() as td:
            root, state, leases, sessions, gui, coordinator = self._make(td)
            base = dt.datetime(2026, 9, 11, 10, 0, tzinfo=UTC)

            tick1 = asyncio.run(coordinator.run_once(now=base))
            self.assertEqual("ROUTED", tick1["relay"]["status"])
            self.assertEqual(1, state.load()["state_version"])
            lease1 = leases.load("project-coordinator")
            self.assertEqual("ACTIVE", lease1["status"])
            a1_session = lease1["session_id"]
            self.assertTrue(a1_session.startswith("a-window-v0-"))
            self.assertEqual("A", tick1["relay"]["actor_id"])
            self.assertIsNone(gui.calls[0]["conversation_url"])

            tick2 = asyncio.run(coordinator.run_once(now=base + dt.timedelta(minutes=5)))
            self.assertEqual("ROUTED", tick2["relay"]["status"])
            self.assertEqual(2, state.load()["state_version"])
            drained = leases.load("project-coordinator")
            self.assertEqual("DRAINED", drained["status"])
            self.assertEqual(a1_session, drained["session_id"])
            self.assertEqual("https://chatgpt.com/c/a1-coordinator", gui.calls[1]["conversation_url"])

            tick3 = asyncio.run(coordinator.run_once(now=base + dt.timedelta(minutes=6)))
            self.assertEqual("ROUTED", tick3["relay"]["status"])
            self.assertEqual(2, state.load()["state_version"])
            lease2 = leases.load("project-coordinator")
            self.assertEqual("ACTIVE", lease2["status"])
            self.assertNotEqual(a1_session, lease2["session_id"])
            self.assertTrue(lease2["session_id"].startswith("a-window-v2-"))
            self.assertEqual("A", tick3["relay"]["actor_id"])
            self.assertEqual("https://chatgpt.com/c/a1-coordinator", gui.calls[2]["conversation_url"])
            self.assertEqual("https://chatgpt.com/c/a1-coordinator", sessions.get_master_conversation_url("project-coordinator"))
            self.assertEqual(3, len(gui.calls))

    def test_terminal_project_does_not_create_master_lease_or_gui_call(self):
        with tempfile.TemporaryDirectory() as td:
            root, state, leases, sessions, gui, coordinator = self._make(td)
            state.update(0, {"status": "COMPLETE", "next_exact_action": "none"})
            result = asyncio.run(coordinator.run_once(now=dt.datetime(2026, 9, 11, 10, 0, tzinfo=UTC)))
            self.assertEqual("COMPLETE", result["status"])
            self.assertIsNone(leases.load("project-coordinator"))
            self.assertEqual([], gui.calls)


if __name__ == "__main__":
    unittest.main()
