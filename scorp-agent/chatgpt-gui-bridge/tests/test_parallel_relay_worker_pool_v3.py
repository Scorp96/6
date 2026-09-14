import asyncio
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from session_registry_v3 import SessionRegistryV3
from worker_conversation_pool_v3 import WorkerConversationPoolV3

UTC = dt.timezone.utc


def worker_turn(turn_id, actor_id, session_id, objective):
    return {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": turn_id,
        "project_id": "pool-project",
        "state_version": 0,
        "actor_kind": "WORKER",
        "actor_id": actor_id,
        "session_id": session_id,
        "root_objective_sha256": "r" * 64,
        "acceptance_sha256": "a" * 64,
        "objective_sha256": objective,
        "payload": {
            "event": "ASSIGNMENT",
            "assignment": {"task": f"task for {actor_id}"},
        },
    }


class PoolTransport:
    def __init__(self):
        self.submit_calls = []
        self.completions = {}
        self.slot_url = "https://chatgpt.com/c/persistent-worker-slot"

    async def submit(self, *, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.submit_calls.append({
            "turn_id": turn_id,
            "actor_kind": actor_kind,
            "conversation_url": conversation_url,
            "prompt": prompt,
        })
        url = conversation_url or self.slot_url
        return {
            "status": "SUBMITTED",
            "turn_id": turn_id,
            "submission_id": f"submit-{turn_id}",
            "conversation_url": url,
            "handle": {
                "submission_id": f"submit-{turn_id}",
                "turn_id": turn_id,
                "actor_kind": actor_kind,
                "conversation_url": url,
            },
        }

    async def poll(self, turn_id, *, timeout_seconds=1800):
        if turn_id not in self.completions:
            return {"status": "PENDING", "turn_id": turn_id}
        return {
            "status": "COMPLETED",
            "turn_id": turn_id,
            "submission_id": f"submit-{turn_id}",
            "response": self.completions[turn_id],
            "snapshot": f"terminal:{turn_id}",
            "conversation_url": self.slot_url,
        }


class ParallelRelayWorkerPoolV3Tests(unittest.TestCase):
    def test_completed_worker_releases_slot_and_next_worker_reuses_same_conversation_without_session_ownership(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outbox = root / "master-worker-v3-outbox"
            outbox.mkdir(parents=True)
            sessions = SessionRegistryV3(root / "sessions-v3.json")
            pool = WorkerConversationPoolV3(root / "worker-conversation-pool-v3.json", pool_size=1)
            transport = PoolTransport()
            relay = ParallelMasterWorkerRelayV3(
                root,
                sessions,
                transport,
                max_inflight=1,
                max_workers=1,
                worker_pool=pool,
            )
            now = dt.datetime(2026, 9, 12, 8, 20, tzinfo=UTC)

            alpha = worker_turn("turn-alpha", "worker-alpha", "session-alpha", "1" * 64)
            (outbox / "turn-alpha.json").write_text(json.dumps(alpha), encoding="utf-8")
            first = asyncio.run(relay.run_tick(now=now))
            self.assertEqual(1, first["submitted"])
            self.assertIsNone(transport.submit_calls[0]["conversation_url"])
            lease = pool.resolve("pool-project", "worker-alpha", "session-alpha", "turn-alpha")
            self.assertEqual("worker-slot-1", lease["slot_id"])
            self.assertEqual(transport.slot_url, lease["conversation_url"])

            transport.completions["turn-alpha"] = {
                "kind": "HANDOFF",
                "completed": ["alpha done"],
                "evidence": ["alpha evidence"],
            }
            finished = asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=1)))
            self.assertEqual(1, finished["completed"])
            self.assertIsNone(pool.resolve("pool-project", "worker-alpha", "session-alpha", "turn-alpha"))
            self.assertIsNone(sessions.get_session("pool-project", "session-alpha"))

            beta = worker_turn("turn-beta", "worker-beta", "session-beta", "2" * 64)
            (outbox / "turn-beta.json").write_text(json.dumps(beta), encoding="utf-8")
            second = asyncio.run(relay.run_tick(now=now + dt.timedelta(seconds=2)))
            self.assertEqual(1, second["submitted"])
            self.assertEqual(2, len(transport.submit_calls))
            self.assertEqual(transport.slot_url, transport.submit_calls[1]["conversation_url"])
            beta_lease = pool.resolve("pool-project", "worker-beta", "session-beta", "turn-beta")
            self.assertEqual("worker-slot-1", beta_lease["slot_id"])
            self.assertEqual(transport.slot_url, beta_lease["conversation_url"])
            self.assertIsNone(sessions.get_session("pool-project", "session-beta"))

    def test_worker_pool_exhaustion_defers_extra_worker_without_transport_submit(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outbox = root / "master-worker-v3-outbox"
            outbox.mkdir(parents=True)
            sessions = SessionRegistryV3(root / "sessions-v3.json")
            pool = WorkerConversationPoolV3(root / "worker-conversation-pool-v3.json", pool_size=1)
            transport = PoolTransport()
            relay = ParallelMasterWorkerRelayV3(
                root,
                sessions,
                transport,
                max_inflight=2,
                max_workers=2,
                worker_pool=pool,
            )
            now = dt.datetime(2026, 9, 12, 8, 20, tzinfo=UTC)
            for turn_id, actor_id, session_id, objective in (
                ("turn-alpha", "worker-alpha", "session-alpha", "1" * 64),
                ("turn-beta", "worker-beta", "session-beta", "2" * 64),
            ):
                value = worker_turn(turn_id, actor_id, session_id, objective)
                (outbox / f"{turn_id}.json").write_text(json.dumps(value), encoding="utf-8")
            result = asyncio.run(relay.run_tick(now=now))
            self.assertEqual(1, result["submitted"])
            self.assertEqual(1, result["deferred"])
            self.assertEqual(1, len(transport.submit_calls))


if __name__ == "__main__":
    unittest.main()
