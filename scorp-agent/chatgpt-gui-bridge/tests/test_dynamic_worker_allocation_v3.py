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

UTC = dt.timezone.utc


class FakeTransport:
    def __init__(self):
        self.submit_calls = []
        self.completions = {}
        self.urls = {}

    async def submit(self, *, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.submit_calls.append((turn_id, actor_kind, conversation_url))
        url = conversation_url or self.urls.setdefault(turn_id, f"https://chatgpt.com/c/{turn_id}")
        return {
            "status": "SUBMITTED",
            "turn_id": turn_id,
            "submission_id": f"submit-{turn_id}",
            "conversation_url": url,
            "handle": {"turn_id": turn_id, "conversation_url": url},
        }

    async def poll(self, turn_id, *, timeout_seconds=1800):
        if turn_id not in self.completions:
            return {"status": "PENDING", "turn_id": turn_id}
        return {
            "status": "COMPLETED",
            "turn_id": turn_id,
            "response": self.completions[turn_id],
            "snapshot": f"done:{turn_id}",
            "conversation_url": self.urls.get(turn_id, f"https://chatgpt.com/c/{turn_id}"),
        }


class DynamicWorkerAllocationV3Tests(unittest.TestCase):
    def _state(self):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 0,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "DYNAMIC_WORKERS",
            "next_exact_action": "dispatch bounded work",
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
            "turn_id": "master-root",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-0",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {"event": "RESUME_MASTER"},
        }

    def _make(self, td):
        root = pathlib.Path(td)
        store = ProjectStateStore(root / "project-state.json")
        store.create(self._state())
        leases = MasterWindowLeaseStore(root / "master-lease.json")
        now = dt.datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-0", "master-root", 0, now=now, ttl_seconds=1500)
        sessions = SessionRegistryV3(root / "sessions.json")
        transition = MasterStateTransitionV3(store, leases)
        transport = FakeTransport()
        relay = ParallelMasterWorkerRelayV3(
            root,
            sessions,
            transport,
            master_transition=transition,
            max_inflight=4,
            max_workers=3,
        )
        relay._write_child(self._master_turn())
        return root, store, transport, relay, now

    def _complete_dispatch(self, transport, relay, now, assignments):
        asyncio.run(relay.run_tick(now=now))
        transport.completions["master-root"] = {
            "kind": "DISPATCH",
            "assignments": assignments,
            "state_patch": {"next_exact_action": "review worker events"},
        }
        return asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=1)))

    @staticmethod
    def _turn(root, turn_id):
        return json.loads(
            (root / "master-worker-v3-outbox" / f"{turn_id}.json").read_text(encoding="utf-8-sig")
        )

    def test_only_three_workers_run_concurrently_and_fourth_stays_queued(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, transport, relay, now = self._make(td)
            assignments = [
                {"objective_sha256": f"{i:064x}", "task": f"task-{i}", "resource_scope": [f"resource-{i}"]}
                for i in range(1, 5)
            ]
            tick = self._complete_dispatch(transport, relay, now, assignments)
            worker_calls = [row for row in transport.submit_calls if row[1] == "WORKER"]
            master_calls = [row for row in transport.submit_calls if row[1] == "MASTER" and row[0] != "master-root"]
            self.assertEqual(3, len(worker_calls))
            self.assertEqual(1, len(master_calls))
            self.assertEqual(4, tick["inflight"])
            submitted_ids = {row[0] for row in worker_calls}
            all_worker_turns = {
                p.stem
                for p in (root / "master-worker-v3-outbox").glob("*.json")
                if json.loads(p.read_text(encoding="utf-8-sig")).get("actor_kind") == "WORKER"
            }
            self.assertEqual(4, len(all_worker_turns))
            self.assertEqual(1, len(all_worker_turns - submitted_ids))

    def test_worker_slot_is_refilled_after_one_worker_completes(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, transport, relay, now = self._make(td)
            assignments = [
                {"objective_sha256": f"{i:064x}", "task": f"task-{i}", "resource_scope": [f"resource-{i}"]}
                for i in range(1, 5)
            ]
            self._complete_dispatch(transport, relay, now, assignments)
            first_three = [row[0] for row in transport.submit_calls if row[1] == "WORKER"]
            self.assertEqual(3, len(first_three))
            transport.completions[first_three[0]] = {"kind": "HANDOFF", "evidence": ["done"]}
            asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=2)))
            worker_calls = [row[0] for row in transport.submit_calls if row[1] == "WORKER"]
            self.assertEqual(4, len(worker_calls))
            self.assertEqual(4, len(set(worker_calls)))

    def test_overlapping_resource_scope_is_serialized_while_disjoint_scope_runs(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, transport, relay, now = self._make(td)
            assignments = [
                {"objective_sha256": "1" * 64, "task": "write A first", "resource_scope": ["file:A"]},
                {"objective_sha256": "2" * 64, "task": "write A second", "resource_scope": ["file:A"]},
                {"objective_sha256": "3" * 64, "task": "write B", "resource_scope": ["file:B"]},
            ]
            self._complete_dispatch(transport, relay, now, assignments)
            worker_calls = [row[0] for row in transport.submit_calls if row[1] == "WORKER"]
            scopes = [set((self._turn(root, tid).get("payload") or {}).get("assignment", {}).get("resource_scope") or []) for tid in worker_calls]
            self.assertEqual(2, len(worker_calls))
            self.assertIn({"file:B"}, scopes)
            self.assertEqual(1, sum(scope == {"file:A"} for scope in scopes))
            pending_a = [
                p.stem
                for p in (root / "master-worker-v3-outbox").glob("*.json")
                if json.loads(p.read_text(encoding="utf-8-sig")).get("actor_kind") == "WORKER"
                and set((json.loads(p.read_text(encoding="utf-8-sig")).get("payload") or {}).get("assignment", {}).get("resource_scope") or []) == {"file:A"}
                and p.stem not in set(worker_calls)
            ]
            self.assertEqual(1, len(pending_a))
            running_a = next(tid for tid in worker_calls if set((self._turn(root, tid).get("payload") or {}).get("assignment", {}).get("resource_scope") or []) == {"file:A"})
            transport.completions[running_a] = {"kind": "HANDOFF", "evidence": ["A done"]}
            asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=2)))
            worker_calls_after = [row[0] for row in transport.submit_calls if row[1] == "WORKER"]
            self.assertIn(pending_a[0], worker_calls_after)


if __name__ == "__main__":
    unittest.main()
