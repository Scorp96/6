import asyncio
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3
from worker_conversation_pool_v3 import WorkerConversationPoolV3

UTC = dt.timezone.utc


class FakeTransport:
    def __init__(self):
        self.submit_calls = []
        self.poll_calls = []
        self.completions = {}
        self.urls = {"master-0": "https://chatgpt.com/c/a-window-zero"}

    async def submit(self, *, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.submit_calls.append((turn_id, actor_kind, conversation_url))
        url = conversation_url or self.urls.setdefault(turn_id, f"https://chatgpt.com/c/{turn_id}")
        return {
            "status": "SUBMITTED",
            "turn_id": turn_id,
            "submission_id": f"submit-{turn_id}",
            "conversation_url": url,
            "handle": {"submission_id": f"submit-{turn_id}", "turn_id": turn_id, "conversation_url": url},
        }

    async def poll(self, turn_id, *, timeout_seconds=1800):
        self.poll_calls.append(turn_id)
        if turn_id not in self.completions:
            return {"status": "PENDING", "turn_id": turn_id, "submission_id": f"submit-{turn_id}"}
        value = self.completions[turn_id]
        return {
            "status": "COMPLETED",
            "turn_id": turn_id,
            "submission_id": f"submit-{turn_id}",
            "response": value,
            "snapshot": f"terminal:{turn_id}",
            "conversation_url": self.urls.get(turn_id, f"https://chatgpt.com/c/{turn_id}"),
        }


class TimedOutTransport(FakeTransport):
    async def poll(self, turn_id, *, timeout_seconds=1800):
        self.poll_calls.append(turn_id)
        return {
            "status": "TIMED_OUT",
            "turn_id": turn_id,
            "reason": "GUI_TIMEOUT",
            "timed_out_at": "2026-09-11T11:30:00Z",
        }


class ParallelMasterWorkerRelayV3Tests(unittest.TestCase):
    def _state(self):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 0,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "PARALLEL_TRANSPORT",
            "next_exact_action": "dispatch workers",
            "active_master_window": None,
            "active_workers": [],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

    def _master_turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "master-0",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-0",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {"event": "RESUME_MASTER"},
        }

    def _make(self, td, max_inflight=3):
        root = pathlib.Path(td)
        store = ProjectStateStore(root / "project-state.json")
        store.create(self._state())
        leases = MasterWindowLeaseStore(root / "master-lease.json")
        now = dt.datetime(2026, 9, 11, 11, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-0", "master-0", 0, now=now, ttl_seconds=1500)
        sessions = SessionRegistryV3(root / "sessions.json")
        tx = MasterStateTransitionV3(store, leases)
        transport = FakeTransport()
        relay = ParallelMasterWorkerRelayV3(
            root,
            sessions,
            transport,
            master_transition=tx,
            max_inflight=max_inflight,
        )
        turn = self._master_turn()
        relay._write_child(turn)
        return root, store, leases, sessions, transport, relay, now

    def test_dispatch_completion_submits_two_workers_and_master_core_before_any_child_completes(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, _, _, transport, relay, now = self._make(td, max_inflight=3)
            first = asyncio.run(relay.run_tick(now=now))
            self.assertEqual(1, first["submitted"])
            self.assertEqual(["master-0"], [row[0] for row in transport.submit_calls])

            transport.urls["master-0"] = "https://chatgpt.com/c/a-window-zero"
            transport.completions["master-0"] = {
                "kind": "DISPATCH",
                "assignments": [
                    {"objective_sha256": "1" * 64, "task": "worker one"},
                    {"objective_sha256": "2" * 64, "task": "worker two"},
                ],
                "state_patch": {"next_exact_action": "continue core while workers run"},
            }
            second = asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=1)))
            self.assertEqual(1, second["completed"])
            self.assertEqual(3, second["submitted"])
            self.assertEqual(1, store.load()["state_version"])

            child_calls = transport.submit_calls[1:]
            worker_calls = [row for row in child_calls if row[1] == "WORKER"]
            master_calls = [row for row in child_calls if row[1] == "MASTER"]
            self.assertEqual(2, len(worker_calls))
            self.assertEqual(1, len(master_calls))
            self.assertEqual("https://chatgpt.com/c/a-window-zero", master_calls[0][2])
            self.assertEqual(3, second["inflight"])
            ledger = json.loads((root / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            for turn_id, _, _ in child_calls:
                self.assertEqual("GUI_SUBMITTED", ledger[turn_id]["state"])

    def test_one_worker_completion_routes_event_while_other_worker_remains_inflight_without_resubmit(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, _, _, transport, relay, now = self._make(td, max_inflight=3)
            asyncio.run(relay.run_tick(now=now))
            transport.urls["master-0"] = "https://chatgpt.com/c/a-window-zero"
            transport.completions["master-0"] = {
                "kind": "DISPATCH",
                "assignments": [
                    {"objective_sha256": "1" * 64},
                    {"objective_sha256": "2" * 64},
                ],
                "state_patch": {"next_exact_action": "parallel"},
            }
            asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=1)))
            workers = [row[0] for row in transport.submit_calls if row[1] == "WORKER"]
            self.assertEqual(2, len(workers))
            before_submit_count = len(transport.submit_calls)
            transport.completions[workers[0]] = {"kind": "HANDOFF", "evidence": ["worker one done"]}
            tick = asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=2)))
            self.assertGreaterEqual(tick["completed"], 1)
            self.assertEqual(before_submit_count, len(transport.submit_calls))
            pending_events = relay.worker_events.pending("p")
            self.assertEqual(1, len(pending_events))
            self.assertEqual(workers[0], pending_events[0]["source_turn_id"])
            ledger = json.loads((root / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("ROUTED", ledger[workers[0]]["state"])
            self.assertEqual("GUI_SUBMITTED", ledger[workers[1]]["state"])

    def test_legacy_inflight_worker_without_pool_lease_late_acquires_for_timeout_terminalization(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            state = self._state()
            state["active_workers"] = [{
                "worker_id": "worker-legacy",
                "session_id": "worker-legacy-session",
                "objective_sha256": "1" * 64,
            }]
            store = ProjectStateStore(root / "project-state.json")
            store.create(state)
            leases = MasterWindowLeaseStore(root / "master-lease.json")
            sessions = SessionRegistryV3(root / "sessions.json")
            tx = MasterStateTransitionV3(store, leases)
            transport = TimedOutTransport()
            pool_path = root / "worker-conversation-pool-v3.json"
            pool = WorkerConversationPoolV3(pool_path, pool_size=3)
            relay = ParallelMasterWorkerRelayV3(
                root,
                sessions,
                transport,
                master_transition=tx,
                max_inflight=3,
                worker_pool=pool,
            )
            turn = {
                "protocol_version": "scorp.master-worker/turn-v3",
                "turn_id": "worker-legacy-turn",
                "project_id": "p",
                "state_version": 0,
                "actor_kind": "WORKER",
                "actor_id": "worker-legacy",
                "session_id": "worker-legacy-session",
                "root_objective_sha256": "g" * 64,
                "acceptance_sha256": "a" * 64,
                "objective_sha256": "1" * 64,
                "payload": {"event": "ASSIGNMENT", "assignment": {"objective_sha256": "1" * 64}},
            }
            relay._write_child(turn)
            relay._save_ledger({
                "worker-legacy-turn": {
                    "state": "GUI_SUBMITTED",
                    "actor_kind": "WORKER",
                    "actor_id": "worker-legacy",
                    "session_id": "worker-legacy-session",
                    "conversation_url": "https://chatgpt.com/c/legacy-worker",
                    "submitted_at": "2026-09-11T10:00:00Z",
                }
            })
            self.assertFalse(pool_path.exists())
            tick = asyncio.run(relay.run_tick(now=dt.datetime(2026, 9, 11, 11, 30, tzinfo=UTC)))
            self.assertGreaterEqual(tick["completed"], 1)
            self.assertEqual([], transport.submit_calls)
            ledger = json.loads((root / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("ROUTED", ledger["worker-legacy-turn"]["state"])
            self.assertEqual("GUI_TIMEOUT", ledger["worker-legacy-turn"]["timeout_reason"])
            self.assertEqual([], pool.active_leases("p"))
            self.assertTrue(pool_path.is_file())
            pending = relay.worker_events.pending("p")
            self.assertEqual(1, len(pending))
            self.assertEqual("worker-legacy-turn", pending[0]["source_turn_id"])

    def test_max_inflight_caps_remote_generations(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, _, _, transport, relay, now = self._make(td, max_inflight=1)
            tick = asyncio.run(relay.run_tick(now=now))
            self.assertEqual(1, tick["submitted"])
            self.assertEqual(1, tick["inflight"])
            again = asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=1)))
            self.assertEqual(0, again["submitted"])
            self.assertEqual(1, again["inflight"])
            self.assertEqual(1, len(transport.submit_calls))

    def test_never_submits_second_master_while_master_is_inflight(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, _, _, transport, relay, now = self._make(td, max_inflight=3)
            second_master = dict(self._master_turn())
            second_master["turn_id"] = "master-0-second"
            second_master["payload"] = {"event": "CONTINUE_CORE"}
            relay._write_child(second_master)
            tick = asyncio.run(relay.run_tick(now=now))
            master_submits = [row for row in transport.submit_calls if row[1] == "MASTER"]
            self.assertEqual(1, len(master_submits))
            self.assertEqual(1, tick["inflight"])


if __name__ == "__main__":
    unittest.main()
