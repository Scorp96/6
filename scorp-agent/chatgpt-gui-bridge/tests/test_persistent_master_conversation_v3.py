import pathlib
import tempfile
import unittest

from session_registry_v3 import SessionRegistryV3


class PersistentMasterConversationV3Tests(unittest.TestCase):
    def test_master_sessions_reuse_one_canonical_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            url = "https://chatgpt.com/c/persistent-a-111"
            self.assertEqual(
                url,
                reg.record_session("p", "a-window-1", "MASTER", "A", "t1", url),
            )
            self.assertEqual(
                url,
                reg.record_session("p", "a-window-2", "MASTER", "A", "t2", url),
            )
            self.assertEqual(url, reg.get_master_conversation_url("p"))
            self.assertEqual(url, reg.get_session_url("p", "a-window-1"))
            self.assertEqual(url, reg.get_session_url("p", "a-window-2"))

    def test_worker_cannot_reuse_master_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            url = "https://chatgpt.com/c/persistent-a-222"
            reg.record_session("p", "a-window-1", "MASTER", "A", "t1", url)
            with self.assertRaisesRegex(ValueError, "SESSION_CONVERSATION_OWNERSHIP_CONFLICT"):
                reg.record_session(
                    "p", "worker-1-session", "WORKER", "worker-001", "tw1", url
                )

    def test_other_project_cannot_reuse_master_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            url = "https://chatgpt.com/c/persistent-a-333"
            reg.record_session("p1", "a-window-1", "MASTER", "A", "t1", url)
            with self.assertRaisesRegex(ValueError, "SESSION_CONVERSATION_OWNERSHIP_CONFLICT"):
                reg.record_session("p2", "a-window-1", "MASTER", "A", "t2", url)

    def test_master_conversation_change_requires_explicit_rotation(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            old = "https://chatgpt.com/c/persistent-a-old"
            new = "https://chatgpt.com/c/persistent-a-new"
            reg.record_session("p", "a-window-1", "MASTER", "A", "t1", old)
            with self.assertRaisesRegex(ValueError, "MASTER_CONVERSATION_ROTATION_REQUIRED"):
                reg.record_session("p", "a-window-2", "MASTER", "A", "t2", new)
            rotated = reg.rotate_master_conversation(
                "p",
                predecessor_url=old,
                conversation_url=new,
                reason="ACTOR_GUI_HARD_TIMEOUT",
                turn_id="rotate-t2",
            )
            self.assertEqual(new, rotated)
            self.assertEqual(
                new,
                reg.record_session("p", "a-window-2", "MASTER", "A", "t2", new),
            )
            binding = reg.get_master_conversation("p")
            self.assertEqual(old, binding["predecessor_conversation_url"])
            self.assertEqual("ACTOR_GUI_HARD_TIMEOUT", binding["rotation_reason"])
            self.assertEqual("rotate-t2", binding["rotation_turn_id"])


if __name__ == "__main__":
    unittest.main()
