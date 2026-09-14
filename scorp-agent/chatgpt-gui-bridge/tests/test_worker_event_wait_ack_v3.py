import asyncio
import datetime as dt
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_relay_v3 import _sha
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc


class NoSubmitTransport:
    def __init__(self):
        self.submit_calls = []
        self.poll_calls = []

    async def submit(self, **kwargs):
        self.submit_calls.append(kwargs)
        raise AssertionError("HISTORICAL_EVENT_WAIT_MUST_NOT_RESUBMIT")

    async def poll(self, turn_id, *, timeout_seconds=1800):
        self.poll_calls.append(turn_id)
        raise AssertionError("HISTORICAL_EVENT_WAIT_MUST_NOT_POLL_REMOTE")


class WorkerEventWaitAckV3Tests(unittest.TestCase):
    def _state(self):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 0,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "WORKER_ACTIVE",
            "next_exact_action": "wait for worker",
            "active_master_window": None,
            "active_workers": [
                {
                    "worker_id": "worker-one",
                    "session_id": "worker-one-session",
                    "objective_sha256": "1" * 64,
                }
            ],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

    def _worker_turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-one-turn",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "WORKER",
            "actor_id": "worker-one",
            "session_id": "worker-one-session",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {
                "event": "ASSIGNMENT",
                "assignment": {"objective_sha256": "1" * 64},
            },
        }

    def _make(self, td):
        root = pathlib.Path(td)
        store = ProjectStateStore(root / "project-state.json")
        store.create(self._state())
        leases = MasterWindowLeaseStore(root / "master-window-lease.json")
        now = dt.datetime(2026, 9, 13, 2, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-0", "resume-a-0", 0, now=now, ttl_seconds=1500)
        queue = WorkerEventQueueV3(root / "worker-events-v3")
        worker_turn = self._worker_turn()
        queued = queue.enqueue(
            worker_turn,
            {"kind": "BLOCKER", "reason": "ACTOR_GUI_TIMEOUT", "source_turn_id": worker_turn["turn_id"]},
        )
        tx = MasterStateTransitionV3(store, leases, queue)
        sessions = SessionRegistryV3(root / "sessions-v3.json")
        transport = NoSubmitTransport()
        relay = ParallelMasterWorkerRelayV3(
            root,
            sessions,
            transport,
            master_transition=tx,
            max_inflight=3,
        )
        master_turn = {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-event-master-0",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-0",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {
                "event": "WORKER_BLOCKER",
                "worker_event_id": queued["event_id"],
                "worker_id": "worker-one",
                "source_worker_turn_id": worker_turn["turn_id"],
            },
        }
        return root, store, leases, queue, transport, relay, master_turn, queued["event_id"], now

    def test_worker_event_wait_commits_ack_and_removes_finished_worker(self):
        with tempfile.TemporaryDirectory() as td:
            _, store, leases, queue, transport, relay, master_turn, event_id, now = self._make(td)
            result = relay._commit_master_response(
                master_turn,
                {"kind": "WAIT"},
                now=now + dt.timedelta(seconds=1),
            )
            self.assertIsNotNone(result)
            self.assertEqual(1, result["state"]["state_version"])
            self.assertEqual([], result["state"]["active_workers"])
            self.assertEqual("ACKED", queue.get(event_id)["state"])
            self.assertEqual(result["transition_id"], queue.get(event_id)["ack_transition_id"])
            self.assertTrue(leases.is_active("p", now=now + dt.timedelta(seconds=1)))
            self.assertEqual([], transport.submit_calls)
            self.assertEqual(1, store.load()["state_version"])

    def test_plain_wait_remains_nontransitioning(self):
        with tempfile.TemporaryDirectory() as td:
            _, store, _, _, transport, relay, master_turn, _, now = self._make(td)
            plain_turn = dict(master_turn)
            plain_turn["turn_id"] = "master-plain-wait"
            plain_turn["payload"] = {"event": "CONTINUE_CORE"}
            result = relay._commit_master_response(
                plain_turn,
                {"kind": "WAIT"},
                now=now + dt.timedelta(seconds=1),
            )
            self.assertIsNone(result)
            self.assertEqual(0, store.load()["state_version"])
            self.assertEqual(1, len(store.load()["active_workers"]))
            self.assertEqual([], transport.submit_calls)

    def test_routed_worker_event_wait_replays_journal_without_remote_resubmit(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, _, queue, transport, relay, master_turn, event_id, now = self._make(td)
            relay._write_child(master_turn)
            digest = _sha(master_turn)
            response = {"kind": "WAIT"}
            relay.response_journal.record(master_turn["turn_id"], digest, response)
            relay._save_ledger(
                {
                    master_turn["turn_id"]: {
                        "state": "ROUTED",
                        "envelope_sha256": digest,
                        "actor_kind": "MASTER",
                        "actor_id": "A",
                        "session_id": "a-window-0",
                        "conversation_url": "https://chatgpt.com/c/a-window-zero",
                        "response_sha256": _sha(response),
                        "children": [],
                        "routed_at": "2026-09-13T02:00:30Z",
                    }
                }
            )
            tick = asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=2)))
            self.assertEqual(1, tick["recovered"])
            self.assertEqual(0, tick["submitted"])
            self.assertEqual([], transport.submit_calls)
            self.assertEqual([], transport.poll_calls)
            self.assertEqual(1, store.load()["state_version"])
            self.assertEqual([], store.load()["active_workers"])
            event = queue.get(event_id)
            self.assertEqual("ACKED", event["state"])
            ledger = relay._ledger()[master_turn["turn_id"]]
            self.assertEqual("ROUTED", ledger["state"])
            self.assertTrue(ledger.get("master_transition_id"))
            self.assertEqual(1, ledger.get("project_state_version"))
            self.assertTrue((root / "master-worker-v3-inbox" / f"{master_turn['turn_id']}.json").is_file())


if __name__ == "__main__":
    unittest.main()
