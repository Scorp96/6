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


class ScriptedGui:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def __call__(self, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.calls.append((turn_id, actor_kind, conversation_url))
        if not self.rows:
            raise AssertionError("UNEXPECTED_GUI_CALL")
        response, url = self.rows.pop(0)
        return response, "snapshot", url


def master(turn_id, version, session="a-current", event="CONTINUE_CORE"):
    return {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": turn_id,
        "project_id": "p",
        "state_version": version,
        "actor_kind": "MASTER",
        "actor_id": "A",
        "session_id": session,
        "root_objective_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "payload": {"event": event},
    }


def worker(turn_id, version, worker_id="worker-one"):
    return {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": turn_id,
        "project_id": "p",
        "state_version": version,
        "actor_kind": "WORKER",
        "actor_id": worker_id,
        "session_id": worker_id + "-session",
        "root_objective_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "objective_sha256": "1" * 64,
        "payload": {"event": "ASSIGNMENT"},
    }


class MasterWorkerRelaySchedulerV3Tests(unittest.TestCase):
    def _make(self, td, gui):
        root = pathlib.Path(td)
        active_worker = {"worker_id": "worker-one", "session_id": "worker-one-session", "objective_sha256": "1" * 64}
        state = {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 5,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "RUN",
            "next_exact_action": "continue",
            "active_master_window": None,
            "active_workers": [active_worker],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }
        state_path = root / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        store = ProjectStateStore(state_path)
        leases = MasterWindowLeaseStore(root / "lease.json")
        now = dt.datetime.now(UTC)
        leases.acquire("p", "a-current", "resume", 5, now=now, ttl_seconds=3600)
        sessions = SessionRegistryV3(root / "sessions.json")
        tx = MasterStateTransitionV3(store, leases)
        relay = MasterWorkerRelayV3(root, sessions, gui, master_transition=tx)
        return root, store, relay

    def _write(self, root, turn):
        outbox = pathlib.Path(root) / "master-worker-v3-outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"{turn['turn_id']}.json").write_text(json.dumps(turn), encoding="utf-8")

    def test_stale_master_is_durably_superseded_and_worker_runs_instead(self):
        with tempfile.TemporaryDirectory() as td:
            gui = ScriptedGui([({"kind": "HANDOFF", "evidence": ["done"]}, "https://chatgpt.com/c/worker-one")])
            root, store, relay = self._make(td, gui)
            self._write(root, master("aaa-stale-master", 4))
            self._write(root, worker("zzz-worker", 4))

            result = asyncio.run(relay.run_once())
            self.assertEqual("WORKER", result["actor_kind"])
            self.assertEqual("zzz-worker", result["turn_id"])
            self.assertEqual(1, len(gui.calls))

            ledger = json.loads((pathlib.Path(root) / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("SUPERSEDED", ledger["aaa-stale-master"]["state"])
            self.assertEqual("MASTER_STATE_VERSION_STALE", ledger["aaa-stale-master"]["superseded_reason"])

    def test_relay_alternates_worker_then_master_after_prior_master_route(self):
        with tempfile.TemporaryDirectory() as td:
            gui = ScriptedGui([
                ({"kind": "HANDOFF", "evidence": ["done"]}, "https://chatgpt.com/c/worker-one"),
                ({"kind": "WAIT"}, "https://chatgpt.com/c/a-current"),
            ])
            root, store, relay = self._make(td, gui)
            relay.sessions.record_session("p", "a-current", "MASTER", "A", "old", "https://chatgpt.com/c/a-current")
            self._write(root, master("master-core", 5))
            self._write(root, worker("worker-assignment", 4))
            ledger_path = pathlib.Path(root) / "master-worker-v3-ledger.json"
            ledger_path.write_text(json.dumps({
                "previous-master": {
                    "state": "ROUTED",
                    "actor_kind": "MASTER",
                    "routed_at": "2099-01-01T00:00:00Z",
                }
            }), encoding="utf-8")

            first = asyncio.run(relay.run_once())
            self.assertEqual("WORKER", first["actor_kind"])
            self.assertEqual("worker-assignment", first["turn_id"])

            second = asyncio.run(relay.run_once())
            self.assertEqual("MASTER", second["actor_kind"])
            self.assertEqual("master-core", second["turn_id"])
            self.assertEqual(2, len(gui.calls))

    def test_superseded_turn_envelope_change_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            gui = ScriptedGui([({"kind": "HANDOFF"}, "https://chatgpt.com/c/worker-one")])
            root, store, relay = self._make(td, gui)
            stale = master("stale-master", 4)
            self._write(root, stale)
            self._write(root, worker("worker-assignment", 4))
            asyncio.run(relay.run_once())

            stale["payload"] = {"event": "CHANGED"}
            self._write(root, stale)
            with self.assertRaisesRegex(ValueError, "ACTOR_TURN_CONFLICT"):
                asyncio.run(relay.run_once())


if __name__ == "__main__":
    unittest.main()
