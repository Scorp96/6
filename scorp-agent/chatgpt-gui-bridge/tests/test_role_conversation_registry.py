import tempfile
import unittest
from pathlib import Path

from bridge_worker import ConversationRegistry


class RoleConversationRegistryTests(unittest.TestCase):
    def test_role_conversations_are_distinct_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "conversation-registry.json"
            reg = ConversationRegistry(path)
            self.assertIsNone(reg.get_role_url("mission-1", "A"))
            reg.record_role("mission-1", "A", "turn-a1", "https://chatgpt.com/c/a-111")
            reg.record_role("mission-1", "B", "turn-b1", "https://chatgpt.com/c/b-222")
            reg.record_role("mission-1", "C", "turn-c1", "https://chatgpt.com/c/c-333")
            reloaded = ConversationRegistry(path)
            self.assertEqual(reloaded.get_role_url("mission-1", "A"), "https://chatgpt.com/c/a-111")
            self.assertEqual(reloaded.get_role_url("mission-1", "B"), "https://chatgpt.com/c/b-222")
            self.assertEqual(reloaded.get_role_url("mission-1", "C"), "https://chatgpt.com/c/c-333")

    def test_role_reuses_same_conversation_across_turns(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "conversation-registry.json"
            reg = ConversationRegistry(path)
            reg.record_role("mission-1", "B", "turn-b1", "https://chatgpt.com/c/b-222")
            reg.record_role("mission-1", "B", "turn-b2", "https://chatgpt.com/c/b-222")
            self.assertEqual(reg.get_role_url("mission-1", "B"), "https://chatgpt.com/c/b-222")

    def test_invalid_role_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            reg = ConversationRegistry(Path(td) / "conversation-registry.json")
            with self.assertRaisesRegex(ValueError, "ROLE_INVALID"):
                reg.get_role_url("mission-1", "D")
            with self.assertRaisesRegex(ValueError, "ROLE_INVALID"):
                reg.record_role("mission-1", "D", "turn-d1", "https://chatgpt.com/c/d-444")

    def test_two_roles_cannot_own_same_chat(self):
        with tempfile.TemporaryDirectory() as td:
            reg = ConversationRegistry(Path(td) / "conversation-registry.json")
            reg.record_role("mission-1", "A", "turn-a1", "https://chatgpt.com/c/shared-111")
            with self.assertRaisesRegex(ValueError, "ROLE_CONVERSATION_NOT_DISTINCT"):
                reg.record_role("mission-1", "B", "turn-b1", "https://chatgpt.com/c/shared-111")

    def test_same_role_cannot_silently_switch_chat(self):
        with tempfile.TemporaryDirectory() as td:
            reg = ConversationRegistry(Path(td) / "conversation-registry.json")
            reg.record_role("mission-1", "B", "turn-b1", "https://chatgpt.com/c/b-222")
            with self.assertRaisesRegex(ValueError, "ROLE_CONVERSATION_CHANGED"):
                reg.record_role("mission-1", "B", "turn-b2", "https://chatgpt.com/c/b-other")


if __name__ == "__main__":
    unittest.main()
