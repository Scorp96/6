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


class NoSubmitTransport:
    def __init__(self):
        self.submit_calls = []

    async def submit(self, **kwargs):
        self.submit_calls.append(kwargs)
        raise AssertionError("FINISH_RESPONSE_TEST_MUST_NOT_REMOTE_SUBMIT")


class WorkerEventWaitFollowupV3Tests(unittest.TestCase):
    def _make(self, td, *, second_worker=False):
        root = pathlib.Path(td)
        workers = [
            {
                "worker_id": "worker-one",
                "session_id": "worker-one-session",
                "objective_sha256": "1" * 64,
            }
        ]
        if second_worker:
            workers.append(
                {
                    "worker_id": "worker-two",
                    "session_id": "worker-two-session",
                    "objective_sha256": "2" * 64,
                }
            )
        state = {
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
            "active_workers": workers,
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }
        store = ProjectStateStore(root / "project-state.json")
        store.create(state)
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
            "payload": {
                "event": "ASSIGNMENT",
                "assignment": {"objective_sha256": "1" * 64},
            },
        }
        event_id = queue.enqueue(
            worker_turn,
            {"kind": "BLOCKER", "reason": "ACTOR_GUI_TIMEOUT", "source_turn_id": worker_turn["turn_id"]},
        )["event_id"]
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
                "worker_event_id": event_id,
                "source_worker_turn_id": worker_turn["turn_id"],
                "worker_id": "worker-one",
            },
        }
        relay._write_child(master_turn)
        relay._save_ledger(
            {
                master_turn["turn_id"]: {
                    "state": "GUI_SUBMITTED",
                    "envelope_sha256": _sha(master_turn),
                    "actor_kind": "MASTER",
                    "actor_id": "A",
                    "session_id": "a-window-0",
                    "conversation_url": "https://chatgpt.com/c/a-window-zero",
                    "submitted_at": "2026-09-13T02:00:00Z",
                }
            }
        )
        return root, store, queue, relay, transport, master_turn, event_id, now

    def _finish_wait(self, relay, master_turn, now):
        return asyncio.run(
            relay._finish_response(
                master_turn,
                _sha(master_turn),
                {
                    "status": "COMPLETED",
                    "response": {"kind": "WAIT"},
                    "snapshot": None,
                    "conversation_url": "https://chatgpt.com/c/a-window-zero",
                },
                now=now + dt.timedelta(seconds=1),
            )
        )

    def test_last_worker_event_wait_schedules_one_post_event_master_reconciliation(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, queue, relay, transport, master_turn, event_id, now = self._make(td)
            children = self._finish_wait(relay, master_turn, now)
            self.assertEqual(1, len(children))
            child = _load(root / "master-worker-v3-outbox" / f"{children[0]}.json")
            self.assertEqual("MASTER", child["actor_kind"])
            self.assertEqual("A", child["actor_id"])
            self.assertEqual("a-window-0", child["session_id"])
            self.assertEqual(1, child["state_version"])
            self.assertEqual("CONTINUE_CORE_AFTER_WORKER_EVENT", child["payload"]["event"])
            self.assertEqual(master_turn["turn_id"], child["payload"]["parent_turn_id"])
            self.assertEqual(event_id, child["payload"]["worker_event_id"])
            self.assertEqual([], child["payload"]["project_state"]["active_workers"])
            self.assertEqual(1, child["payload"]["project_state_version"])
            self.assertEqual([], store.load()["active_workers"])
            self.assertEqual("ACKED", queue.get(event_id)["state"])
            self.assertEqual([], transport.submit_calls)

    def test_event_wait_does_not_schedule_post_event_reconciliation_while_other_worker_active(self):
        with tempfile.TemporaryDirectory() as td:
            _, store, queue, relay, transport, master_turn, event_id, now = self._make(td, second_worker=True)
            children = self._finish_wait(relay, master_turn, now)
            self.assertEqual([], children)
            self.assertEqual(["worker-two"], [row["worker_id"] for row in store.load()["active_workers"]])
            self.assertEqual("ACKED", queue.get(event_id)["state"])
            self.assertEqual([], transport.submit_calls)


if __name__ == "__main__":
    unittest.main()
