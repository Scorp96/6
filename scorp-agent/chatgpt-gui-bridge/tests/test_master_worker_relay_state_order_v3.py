import asyncio
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_relay_v3 import MasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3

UTC = dt.timezone.utc


class OneShotGui:
    def __init__(self, response, url):
        self.response = response
        self.url = url
        self.calls = 0

    async def __call__(self, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("GUI_CALLED_MORE_THAN_ONCE")
        return self.response, "snapshot", self.url


def _state():
    return {
        "protocol_version": "scorp.project-state/v1",
        "project_id": "project-order",
        "master_identity": "A",
        "state_version": 7,
        "goal_contract_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "status": "ACTIVE",
        "current_phase": "DISPATCH",
        "next_exact_action": "dispatch independent work",
        "active_master_window": None,
        "active_workers": [],
        "completed": [],
        "blocked": [],
        "evidence": [],
        "commits": [],
        "tests": [],
    }


def _turn():
    return {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": "master-order-001",
        "project_id": "project-order",
        "state_version": 7,
        "actor_kind": "MASTER",
        "actor_id": "A",
        "session_id": "a-window-order-7",
        "root_objective_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "payload": {"event": "RESUME_MASTER"},
    }


def _response():
    return {
        "kind": "DISPATCH",
        "state_patch": {"next_exact_action": "continue core while workers execute"},
        "assignments": [
            {"objective_sha256": "1" * 64, "payload": {"task": "audit one"}},
            {"objective_sha256": "2" * 64, "payload": {"task": "audit two"}},
        ],
    }


def _setup(root):
    root = pathlib.Path(root)
    state_path = root / "project-state.json"
    state_path.write_text(json.dumps(_state()), encoding="utf-8")
    store = ProjectStateStore(state_path)
    store.load()
    leases = MasterWindowLeaseStore(root / "master-lease.json")
    now = dt.datetime.now(UTC)
    leases.acquire("project-order", "a-window-order-7", "master-order-001", 7, now=now, ttl_seconds=3600)
    sessions = SessionRegistryV3(root / "sessions.json")
    outbox = root / "master-worker-v3-outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    turn = _turn()
    (outbox / f"{turn['turn_id']}.json").write_text(json.dumps(turn), encoding="utf-8")
    tx = MasterStateTransitionV3(store, leases)
    return store, leases, sessions, tx, turn


class GuardedRelay(MasterWorkerRelayV3):
    def __init__(self, *args, project_store, **kwargs):
        self.project_store_for_test = project_store
        super().__init__(*args, **kwargs)

    def _write_child(self, child):
        if self.project_store_for_test.load()["state_version"] != 8:
            raise AssertionError("CHILD_PUBLISHED_BEFORE_PROJECT_STATE_CAS")
        return super()._write_child(child)


class CrashFirstPublishRelay(GuardedRelay):
    def __init__(self, *args, **kwargs):
        self.crashed = False
        super().__init__(*args, **kwargs)

    def _write_child(self, child):
        if self.project_store_for_test.load()["state_version"] != 8:
            raise AssertionError("CHILD_PUBLISHED_BEFORE_PROJECT_STATE_CAS")
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("INJECTED_CRASH_AFTER_STATE_BEFORE_CHILD")
        return super()._write_child(child)


class MasterWorkerRelayStateOrderV3Tests(unittest.TestCase):
    def test_dispatch_commits_project_state_before_any_child_and_children_use_new_version(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, sessions, tx, turn = _setup(td)
            gui = OneShotGui(_response(), "https://chatgpt.com/c/a-order")
            relay = GuardedRelay(td, sessions, gui, master_transition=tx, project_store=store)
            result = asyncio.run(relay.run_once())

            self.assertEqual("ROUTED", result["status"])
            state = store.load()
            self.assertEqual(8, state["state_version"])
            self.assertEqual(2, len(state["active_workers"]))
            self.assertEqual(3, len(result["children"]))
            child_turns = []
            outbox = pathlib.Path(td) / "master-worker-v3-outbox"
            for child_id in result["children"]:
                child_turns.append(json.loads((outbox / f"{child_id}.json").read_text(encoding="utf-8")))
            self.assertTrue(all(child["state_version"] == 8 for child in child_turns))
            self.assertEqual(1, gui.calls)

    def test_crash_after_state_commit_before_child_publish_replays_without_version_or_gui_duplication(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, sessions, tx, turn = _setup(td)
            gui = OneShotGui(_response(), "https://chatgpt.com/c/a-order-crash")
            first = CrashFirstPublishRelay(td, sessions, gui, master_transition=tx, project_store=store)
            with self.assertRaisesRegex(RuntimeError, "INJECTED_CRASH_AFTER_STATE_BEFORE_CHILD"):
                asyncio.run(first.run_once())

            self.assertEqual(8, store.load()["state_version"])
            self.assertEqual(1, gui.calls)

            second = GuardedRelay(td, sessions, gui, master_transition=tx, project_store=store)
            result = asyncio.run(second.run_once())
            self.assertEqual("ROUTED", result["status"])
            self.assertEqual(8, store.load()["state_version"])
            self.assertEqual(1, gui.calls)
            self.assertEqual(3, len(result["children"]))


if __name__ == "__main__":
    unittest.main()
