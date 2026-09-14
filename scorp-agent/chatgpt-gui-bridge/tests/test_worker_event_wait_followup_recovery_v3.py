import asyncio
import datetime as dt
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_relay_v3 import _load, _sha
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc


class NoRemoteTransport:
    def __init__(self):
        self.submit_calls = []
        self.poll_calls = []

    async def submit(self, **kwargs):
        self.submit_calls.append(kwargs)
        raise AssertionError("ACKED_WAIT_RECOVERY_MUST_NOT_REMOTE_SUBMIT")

    async def poll(self, turn_id, *, timeout_seconds=1800):
        self.poll_calls.append(turn_id)
        raise AssertionError("ACKED_WAIT_RECOVERY_MUST_NOT_REMOTE_POLL")


class WorkerEventWaitFollowupRecoveryV3Tests(unittest.TestCase):
    def test_preupgrade_acked_routed_wait_replays_locally_to_create_missing_followup(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = ProjectStateStore(root / "project-state.json")
            store.create(
                {
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
            )
            leases = MasterWindowLeaseStore(root / "master-window-lease.json")
            now = dt.datetime(2026, 9, 13, 2, 0, tzinfo=UTC)
            leases.acquire("p", "a-window-0", "resume-a-0", 0, now=now, ttl_seconds=1500)
            queue = WorkerEventQueueV3(root / "worker-events-v3")
            worker_turn = {
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
                "payload": {"event": "ASSIGNMENT", "assignment": {"objective_sha256": "1" * 64}},
            }
            event_id = queue.enqueue(
                worker_turn,
                {"kind": "BLOCKER", "reason": "ACTOR_GUI_TIMEOUT", "source_turn_id": worker_turn["turn_id"]},
            )["event_id"]
            tx = MasterStateTransitionV3(store, leases, queue)
            sessions = SessionRegistryV3(root / "sessions-v3.json")
            transport = NoRemoteTransport()
            relay = ParallelMasterWorkerRelayV3(root, sessions, transport, master_transition=tx, max_inflight=3)
            turn = {
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
                    "worker_event_id": event_id,
                    "source_worker_turn_id": worker_turn["turn_id"],
                    "worker_id": "worker-one",
                },
            }
            response = {"kind": "WAIT"}
            transition = tx.apply(turn, response, now=now + dt.timedelta(seconds=1))
            self.assertEqual(1, store.load()["state_version"])
            self.assertEqual([], store.load()["active_workers"])
            self.assertEqual("ACKED", queue.get(event_id)["state"])

            relay._write_child(turn)
            digest = _sha(turn)
            relay.response_journal.record(turn["turn_id"], digest, response)
            relay._save_ledger(
                {
                    turn["turn_id"]: {
                        "state": "ROUTED",
                        "envelope_sha256": digest,
                        "actor_kind": "MASTER",
                        "actor_id": "A",
                        "session_id": "a-window-0",
                        "conversation_url": "https://chatgpt.com/c/a-window-zero",
                        "response_sha256": _sha(response),
                        "children": [],
                        "master_transition_id": transition["transition_id"],
                        "project_state_version": 1,
                        "routed_at": "2026-09-13T02:00:01Z",
                    }
                }
            )

            tick = asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=2)))
            self.assertEqual(1, tick["recovered"])
            self.assertEqual(0, tick["submitted"])
            self.assertEqual([], transport.submit_calls)
            self.assertEqual([], transport.poll_calls)
            self.assertEqual(1, store.load()["state_version"])
            self.assertEqual("ACKED", queue.get(event_id)["state"])

            ledger = relay._ledger()[turn["turn_id"]]
            self.assertEqual(transition["transition_id"], ledger["master_transition_id"])
            self.assertEqual(1, ledger["project_state_version"])
            self.assertEqual(1, len(ledger["children"]))
            child = _load(root / "master-worker-v3-outbox" / f"{ledger['children'][0]}.json")
            self.assertEqual("MASTER", child["actor_kind"])
            self.assertEqual("a-window-0", child["session_id"])
            self.assertEqual(1, child["state_version"])
            self.assertEqual("CONTINUE_CORE_AFTER_WORKER_EVENT", child["payload"]["event"])
            self.assertEqual(event_id, child["payload"]["worker_event_id"])
            self.assertEqual([], child["payload"]["project_state"]["active_workers"])


if __name__ == "__main__":
    unittest.main()
