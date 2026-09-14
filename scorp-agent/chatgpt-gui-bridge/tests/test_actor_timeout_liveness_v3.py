import asyncio
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from durable_actor_transport_v3 import DurableActorTransportV3
from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_relay_v3 import _sha
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3

UTC = dt.timezone.utc


class Backend:
    def __init__(self):
        self.poll_calls = 0

    async def submit(self, submission_id, *, prompt, turn_id, actor_kind, conversation_url, timeout_seconds):
        return {
            "submission_id": submission_id,
            "turn_id": turn_id,
            "conversation_url": conversation_url or f"https://chatgpt.com/c/{turn_id}",
        }

    async def recover(self, submission_id, *, turn_id, actor_kind):
        return None

    async def poll(self, handle, *, turn_id, actor_kind, timeout_seconds):
        self.poll_calls += 1
        return {"status": "PENDING"}


class RelayTransport:
    def __init__(self):
        self.submit_calls = []
        self.poll_calls = []
        self.timeout_calls = []

    async def submit(self, *, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.submit_calls.append((turn_id, actor_kind, conversation_url))
        url = conversation_url or f"https://chatgpt.com/c/{turn_id}"
        return {
            "status": "SUBMITTED",
            "turn_id": turn_id,
            "submission_id": f"submit-{turn_id}",
            "conversation_url": url,
            "handle": {"submission_id": f"submit-{turn_id}", "turn_id": turn_id, "conversation_url": url},
        }

    async def poll(self, turn_id, *, timeout_seconds=1800):
        self.poll_calls.append(turn_id)
        return {"status": "PENDING", "turn_id": turn_id, "submission_id": f"submit-{turn_id}"}

    def mark_timed_out(self, turn_id, *, reason, timed_out_at):
        self.timeout_calls.append((turn_id, reason, timed_out_at))
        return {"status": "TIMED_OUT", "turn_id": turn_id, "submission_id": f"submit-{turn_id}"}


class ActorTimeoutLivenessV3Tests(unittest.TestCase):
    def _state(self, *, workers=None):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 0,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "TIMEOUT_TEST",
            "next_exact_action": "continue",
            "active_master_window": None,
            "active_workers": list(workers or []),
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

    def _worker_turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-timeout",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "WORKER",
            "actor_id": "worker-timeout1",
            "session_id": "worker-timeout1-session",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {"event": "ASSIGNMENT", "assignment": {"objective_sha256": "1" * 64, "resource_scope": ["timeout-scope"]}},
        }

    def _master_turn(self, turn_id, session_id):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": turn_id,
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": session_id,
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {"event": "RESUME_MASTER"},
        }

    def _relay(self, td, *, workers=None, lease_session="a-new"):
        root = pathlib.Path(td)
        store = ProjectStateStore(root / "project-state.json")
        store.create(self._state(workers=workers))
        leases = MasterWindowLeaseStore(root / "master-lease.json")
        now = dt.datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
        leases.acquire("p", lease_session, "resume-new", 0, now=now, ttl_seconds=1500)
        sessions = SessionRegistryV3(root / "sessions.json")
        transport = RelayTransport()
        relay = ParallelMasterWorkerRelayV3(
            root,
            sessions,
            transport,
            master_transition=MasterStateTransitionV3(store, leases),
            max_inflight=4,
            max_workers=3,
        )
        return root, transport, relay, now

    def _seed_inflight(self, root, relay, turn, submitted_at):
        relay._write_child(turn)
        row = {
            "state": "GUI_SUBMITTED",
            "envelope_sha256": _sha(turn),
            "actor_kind": turn["actor_kind"],
            "actor_id": turn["actor_id"],
            "session_id": turn["session_id"],
            "submission_id": f"submit-{turn['turn_id']}",
            "conversation_url": f"https://chatgpt.com/c/{turn['turn_id']}",
            "submitted_at": submitted_at,
        }
        (root / "master-worker-v3-ledger.json").write_text(
            json.dumps({turn["turn_id"]: row}, indent=2) + "\n", encoding="utf-8"
        )

    def test_transport_timeout_is_terminal_and_idempotent_without_repoll_or_resubmit(self):
        with tempfile.TemporaryDirectory() as td:
            backend = Backend()
            transport = DurableActorTransportV3(pathlib.Path(td) / "actor-transport-v3.json", backend)
            kwargs = dict(prompt="hello", turn_id="turn-1", actor_kind="WORKER", conversation_url=None, timeout_seconds=120)
            asyncio.run(transport.submit(**kwargs))
            first = transport.mark_timed_out("turn-1", reason="GUI_TIMEOUT", timed_out_at="2026-09-12T02:00:00Z")
            second = transport.mark_timed_out("turn-1", reason="GUI_TIMEOUT", timed_out_at="2026-09-12T02:00:00Z")
            polled = asyncio.run(transport.poll("turn-1", timeout_seconds=120))
            submitted_again = asyncio.run(transport.submit(**kwargs))
            self.assertEqual("TIMED_OUT", first["status"])
            self.assertEqual(first, second)
            self.assertEqual("TIMED_OUT", polled["status"])
            self.assertEqual("ALREADY_TIMED_OUT", submitted_again["status"])
            self.assertEqual(0, backend.poll_calls)

    def test_expired_worker_pending_becomes_deterministic_blocker_and_leaves_inflight(self):
        with tempfile.TemporaryDirectory() as td:
            worker = self._worker_turn()
            workers = [{"worker_id": worker["actor_id"], "session_id": worker["session_id"], "objective_sha256": worker["objective_sha256"], "assignment": worker["payload"]["assignment"]}]
            root, transport, relay, now = self._relay(td, workers=workers)
            self._seed_inflight(root, relay, worker, (now - dt.timedelta(seconds=1801)).isoformat().replace("+00:00", "Z"))
            result = asyncio.run(relay.run_tick(now=now))
            ledger = json.loads((root / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            events = relay.worker_events.pending("p")
            self.assertEqual(1, result["timed_out"])
            self.assertEqual(0, result["inflight"])
            self.assertEqual("ROUTED", ledger[worker["turn_id"]]["state"])
            self.assertEqual(1, len(events))
            self.assertEqual("BLOCKER", events[0]["response_kind"])
            self.assertEqual("ACTOR_GUI_TIMEOUT", events[0]["worker_response"]["reason"])
            self.assertEqual(worker["turn_id"], transport.timeout_calls[0][0])

    def test_expired_master_pending_is_terminalized_and_does_not_block_new_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root, transport, relay, now = self._relay(td, lease_session="a-new")
            old = self._master_turn("master-old", "a-old")
            new = self._master_turn("resume-new", "a-new")
            self._seed_inflight(root, relay, old, (now - dt.timedelta(seconds=1801)).isoformat().replace("+00:00", "Z"))
            relay._write_child(new)
            result = asyncio.run(relay.run_tick(now=now))
            ledger = json.loads((root / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("GUI_TIMED_OUT", ledger[old["turn_id"]]["state"])
            self.assertIn(("resume-new", "MASTER", "https://chatgpt.com/c/master-old"), transport.submit_calls)
            self.assertEqual("https://chatgpt.com/c/master-old", relay.sessions.get_master_conversation_url("p"))
            self.assertEqual(1, result["timed_out"])
            self.assertEqual(old["turn_id"], transport.timeout_calls[0][0])

    def test_young_pending_actor_is_not_timed_out(self):
        with tempfile.TemporaryDirectory() as td:
            worker = self._worker_turn()
            workers = [{"worker_id": worker["actor_id"], "session_id": worker["session_id"], "objective_sha256": worker["objective_sha256"], "assignment": worker["payload"]["assignment"]}]
            root, transport, relay, now = self._relay(td, workers=workers)
            self._seed_inflight(root, relay, worker, (now - dt.timedelta(seconds=60)).isoformat().replace("+00:00", "Z"))
            result = asyncio.run(relay.run_tick(now=now))
            ledger = json.loads((root / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(0, result.get("timed_out", 0))
            self.assertEqual(1, result["inflight"])
            self.assertEqual("GUI_SUBMITTED", ledger[worker["turn_id"]]["state"])
            self.assertEqual([], transport.timeout_calls)
            self.assertEqual([], relay.worker_events.pending("p"))


if __name__ == "__main__":
    unittest.main()
