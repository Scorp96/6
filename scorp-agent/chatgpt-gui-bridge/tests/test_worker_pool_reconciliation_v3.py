import asyncio
import json
import pathlib
import tempfile
import unittest

from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from session_registry_v3 import SessionRegistryV3
from worker_conversation_pool_v3 import WorkerConversationPoolV3


class IdleTransport:
    def __init__(self):
        self.calls = []

    async def submit(self, **kwargs):
        self.calls.append(("submit", kwargs))
        raise AssertionError("no submit expected")

    async def poll(self, *args, **kwargs):
        self.calls.append(("poll", args, kwargs))
        raise AssertionError("no poll expected")


class WorkerPoolReconciliationV3Tests(unittest.TestCase):
    def test_routed_worker_lease_left_by_crash_is_released_on_next_tick_and_url_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "master-worker-v3-outbox").mkdir(parents=True)
            pool = WorkerConversationPoolV3(root / "worker-conversation-pool-v3.json", pool_size=1)
            pool.acquire("p", "worker-alpha", "session-alpha", "turn-alpha")
            url = "https://chatgpt.com/c/persistent-after-crash"
            pool.bind_conversation("p", "worker-alpha", "session-alpha", "turn-alpha", url)
            (root / "master-worker-v3-ledger.json").write_text(
                json.dumps({"turn-alpha": {"state": "ROUTED", "actor_kind": "WORKER"}}),
                encoding="utf-8",
            )
            relay = ParallelMasterWorkerRelayV3(
                root,
                SessionRegistryV3(root / "sessions-v3.json"),
                IdleTransport(),
                max_inflight=1,
                max_workers=1,
                worker_pool=pool,
            )
            asyncio.run(relay.run_tick())
            self.assertIsNone(pool.resolve("p", "worker-alpha", "session-alpha", "turn-alpha"))
            beta = pool.acquire("p", "worker-beta", "session-beta", "turn-beta")
            self.assertEqual("worker-slot-1", beta["slot_id"])
            self.assertEqual(url, beta["conversation_url"])


if __name__ == "__main__":
    unittest.main()
