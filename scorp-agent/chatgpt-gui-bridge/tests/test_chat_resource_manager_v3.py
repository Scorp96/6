import datetime as dt
import importlib
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

UTC = dt.timezone.utc

try:
    crm = importlib.import_module("chat_resource_manager_v3")
except ModuleNotFoundError:
    crm = None


class ChatResourceManagerV3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "chat-resource-v3.json"
        self.now = dt.datetime(2026, 9, 12, 7, 0, 0, tzinfo=UTC)

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self):
        self.assertIsNotNone(crm, "chat_resource_manager_v3 module is missing")
        return crm.ChatResourceManagerV3(
            self.path,
            creation_cooldown_seconds=60,
            throttle_backoff_seconds=300,
        )

    def test_fresh_new_chat_allowed_then_reservation_starts_cooldown(self):
        manager = self.manager()
        allowed = manager.permission(now=self.now, needs_new_chat=True)
        self.assertTrue(allowed["allowed"])
        manager.reserve_new_chat("turn-1", now=self.now)
        blocked = manager.permission(now=self.now + dt.timedelta(seconds=30), needs_new_chat=True)
        self.assertFalse(blocked["allowed"])
        self.assertEqual("CREATION_COOLDOWN", blocked["reason"])
        self.assertEqual("2026-09-12T07:01:00Z", blocked["not_before"])

    def test_throttle_opens_circuit_and_persists_across_reload(self):
        manager = self.manager()
        manager.note_throttle("CHATGPT_REQUEST_THROTTLED", now=self.now)
        reloaded = self.manager()
        blocked = reloaded.permission(now=self.now + dt.timedelta(seconds=299), needs_new_chat=True)
        self.assertFalse(blocked["allowed"])
        self.assertEqual("THROTTLE_CIRCUIT_OPEN", blocked["reason"])
        self.assertEqual("2026-09-12T07:05:00Z", blocked["not_before"])

    def test_expired_cooldown_and_circuit_allow_new_chat_again(self):
        manager = self.manager()
        manager.reserve_new_chat("turn-1", now=self.now)
        manager.note_throttle("CHATGPT_REQUEST_THROTTLED", now=self.now)
        allowed = manager.permission(now=self.now + dt.timedelta(seconds=301), needs_new_chat=True)
        self.assertTrue(allowed["allowed"])
        self.assertEqual("ALLOWED", allowed["reason"])

    def test_creation_cooldown_does_not_block_existing_conversation_reuse(self):
        manager = self.manager()
        manager.reserve_new_chat("turn-1", now=self.now)
        reuse = manager.permission(now=self.now + dt.timedelta(seconds=1), needs_new_chat=False)
        self.assertTrue(reuse["allowed"])
        self.assertEqual("REUSE_ALLOWED", reuse["reason"])
        new_chat = manager.permission(now=self.now + dt.timedelta(seconds=1), needs_new_chat=True)
        self.assertFalse(new_chat["allowed"])
        self.assertEqual("CREATION_COOLDOWN", new_chat["reason"])

    def test_throttle_circuit_blocks_new_prompt_even_when_reusing_existing_conversation(self):
        manager = self.manager()
        manager.note_throttle("CHATGPT_REQUEST_THROTTLED", now=self.now)
        reuse = manager.permission(now=self.now + dt.timedelta(seconds=1), needs_new_chat=False)
        self.assertFalse(reuse["allowed"])
        self.assertEqual("THROTTLE_CIRCUIT_OPEN", reuse["reason"])
        self.assertEqual("2026-09-12T07:05:00Z", reuse["not_before"])

    def test_naive_now_fails_closed(self):
        manager = self.manager()
        with self.assertRaisesRegex(ValueError, "CHAT_RESOURCE_NOW_MUST_BE_AWARE"):
            manager.permission(now=dt.datetime(2026, 9, 12, 7, 0, 0), needs_new_chat=True)


if __name__ == "__main__":
    unittest.main()
