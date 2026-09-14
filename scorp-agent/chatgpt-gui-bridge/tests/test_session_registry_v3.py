import pathlib
import tempfile
import unittest

from session_registry_v3 import SessionRegistryV3


class SessionRegistryV3Tests(unittest.TestCase):
    def test_master_identity_rotates_only_through_explicit_rotation(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            a1 = reg.record_session(
                "project-1", "a-window-1", "MASTER", "A", "turn-a1",
                "https://chatgpt.com/c/a-111",
            )
            with self.assertRaisesRegex(ValueError, "MASTER_CONVERSATION_ROTATION_REQUIRED"):
                reg.record_session(
                    "project-1", "a-window-2", "MASTER", "A", "turn-a2",
                    "https://chatgpt.com/c/a-222",
                )
            reg.rotate_master_conversation(
                "project-1",
                predecessor_url="https://chatgpt.com/c/a-111",
                conversation_url="https://chatgpt.com/c/a-222",
                reason="PROVENANCE_UNRECOVERABLE",
                turn_id="rotate-a2",
            )
            a2 = reg.record_session(
                "project-1", "a-window-2", "MASTER", "A", "turn-a2",
                "https://chatgpt.com/c/a-222",
            )
            self.assertEqual("https://chatgpt.com/c/a-111", a1)
            self.assertEqual("https://chatgpt.com/c/a-222", a2)
            self.assertEqual(a1, reg.get_session_url("project-1", "a-window-1"))
            self.assertEqual(a2, reg.get_session_url("project-1", "a-window-2"))
            self.assertEqual(a2, reg.get_master_conversation_url("project-1"))
            self.assertEqual("A", reg.get_session("project-1", "a-window-2")["actor_id"])

    def test_same_session_cannot_silently_switch_chat(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            reg.record_session("p", "a-window-1", "MASTER", "A", "t1", "https://chatgpt.com/c/a-111")
            with self.assertRaisesRegex(ValueError, "SESSION_CONVERSATION_CHANGED"):
                reg.record_session("p", "a-window-1", "MASTER", "A", "t2", "https://chatgpt.com/c/a-999")

    def test_same_chat_cannot_be_owned_by_master_and_worker(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            reg.record_session("p", "a-window-1", "MASTER", "A", "t1", "https://chatgpt.com/c/shared-111")
            with self.assertRaisesRegex(ValueError, "SESSION_CONVERSATION_OWNERSHIP_CONFLICT"):
                reg.record_session("p", "worker-1-session", "WORKER", "worker-001", "t2", "https://chatgpt.com/c/shared-111")

    def test_ephemeral_worker_ids_are_dynamic_not_fixed_to_b_or_c(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            url = reg.record_session(
                "p", "worker-789-session", "WORKER", "worker-789", "tw1",
                "https://chatgpt.com/c/w-789",
            )
            self.assertEqual("https://chatgpt.com/c/w-789", url)
            entry = reg.get_session("p", "worker-789-session")
            self.assertEqual("WORKER", entry["actor_kind"])
            self.assertEqual("worker-789", entry["actor_id"])

    def test_master_must_use_a_identity_and_worker_must_not(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            with self.assertRaisesRegex(ValueError, "MASTER_IDENTITY_INVALID"):
                reg.record_session("p", "bad-master", "MASTER", "B", "t1", "https://chatgpt.com/c/bad-1")
            with self.assertRaisesRegex(ValueError, "WORKER_ID_INVALID"):
                reg.record_session("p", "bad-worker", "WORKER", "A", "t2", "https://chatgpt.com/c/bad-2")

    def test_restart_preserves_all_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "sessions.json"
            reg = SessionRegistryV3(path)
            reg.record_session("p", "a-window-1", "MASTER", "A", "t1", "https://chatgpt.com/c/a-111")
            reg.record_session("p", "worker-1-session", "WORKER", "worker-001", "t2", "https://chatgpt.com/c/w-111")
            reloaded = SessionRegistryV3(path)
            self.assertEqual("https://chatgpt.com/c/a-111", reloaded.get_session_url("p", "a-window-1"))
            self.assertEqual("https://chatgpt.com/c/a-111", reloaded.get_master_conversation_url("p"))
            self.assertEqual("https://chatgpt.com/c/w-111", reloaded.get_session_url("p", "worker-1-session"))


if __name__ == "__main__":
    unittest.main()
