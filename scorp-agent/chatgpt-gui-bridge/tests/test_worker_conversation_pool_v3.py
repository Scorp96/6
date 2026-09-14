import pathlib
import tempfile
import unittest

try:
    from worker_conversation_pool_v3 import WorkerConversationPoolV3
except ModuleNotFoundError:
    WorkerConversationPoolV3 = None


class WorkerConversationPoolV3Tests(unittest.TestCase):
    def pool(self, root, size=3):
        self.assertIsNotNone(WorkerConversationPoolV3, "worker_conversation_pool_v3 module is missing")
        return WorkerConversationPoolV3(pathlib.Path(root) / "worker-conversation-pool-v3.json", pool_size=size)

    def test_first_three_workers_get_distinct_slots_and_fourth_waits(self):
        with tempfile.TemporaryDirectory() as td:
            pool = self.pool(td, 3)
            leases = [
                pool.acquire("p", f"worker-{i}", f"session-{i}", f"turn-{i}")
                for i in range(1, 5)
            ]
            self.assertEqual(["worker-slot-1", "worker-slot-2", "worker-slot-3"], [x["slot_id"] for x in leases[:3]])
            self.assertIsNone(leases[3])
            self.assertEqual(3, len({x["slot_id"] for x in leases[:3]}))

    def test_bound_conversation_survives_release_and_is_reused_by_next_logical_worker(self):
        with tempfile.TemporaryDirectory() as td:
            pool = self.pool(td, 1)
            first = pool.acquire("p", "worker-alpha", "session-alpha", "turn-alpha")
            self.assertIsNone(first["conversation_url"])
            url = "https://chatgpt.com/c/worker-slot-persistent"
            pool.bind_conversation("p", "worker-alpha", "session-alpha", "turn-alpha", url)
            released = pool.release("p", "worker-alpha", "session-alpha", "turn-alpha")
            self.assertEqual(url, released["conversation_url"])
            second = pool.acquire("p", "worker-beta", "session-beta", "turn-beta")
            self.assertEqual("worker-slot-1", second["slot_id"])
            self.assertEqual(url, second["conversation_url"])
            self.assertEqual("worker-beta", second["actor_id"])

    def test_same_lease_acquire_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            pool = self.pool(td, 2)
            first = pool.acquire("p", "worker-alpha", "session-alpha", "turn-alpha")
            second = pool.acquire("p", "worker-alpha", "session-alpha", "turn-alpha")
            self.assertEqual(first, second)

    def test_restart_preserves_slot_url_and_active_lease(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "worker-conversation-pool-v3.json"
            pool = WorkerConversationPoolV3(path, pool_size=2)
            pool.acquire("p", "worker-alpha", "session-alpha", "turn-alpha")
            url = "https://chatgpt.com/c/restart-worker-slot"
            pool.bind_conversation("p", "worker-alpha", "session-alpha", "turn-alpha", url)
            reloaded = WorkerConversationPoolV3(path, pool_size=2)
            lease = reloaded.resolve("p", "worker-alpha", "session-alpha", "turn-alpha")
            self.assertEqual("worker-slot-1", lease["slot_id"])
            self.assertEqual(url, lease["conversation_url"])

    def test_wrong_worker_cannot_bind_or_release_another_lease(self):
        with tempfile.TemporaryDirectory() as td:
            pool = self.pool(td, 1)
            pool.acquire("p", "worker-alpha", "session-alpha", "turn-alpha")
            with self.assertRaisesRegex(ValueError, "WORKER_SLOT_LEASE_MISMATCH"):
                pool.bind_conversation(
                    "p", "worker-beta", "session-beta", "turn-beta",
                    "https://chatgpt.com/c/not-yours",
                )
            with self.assertRaisesRegex(ValueError, "WORKER_SLOT_LEASE_MISMATCH"):
                pool.release("p", "worker-beta", "session-beta", "turn-beta")


if __name__ == "__main__":
    unittest.main()
