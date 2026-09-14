import unittest

from gui_transport import render_actor_prompt_v3


class ActorPromptV3Tests(unittest.TestCase):
    def _master(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "resume-a-001",
            "project_id": "project-1",
            "state_version": 7,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-v7",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {"event": "RESUME_MASTER", "next_exact_action": "run tests"},
        }

    def test_master_prompt_treats_a_as_identity_not_chat(self):
        prompt = render_actor_prompt_v3(self._master())
        self.assertIn("MASTER_IDENTITY=A", prompt)
        self.assertIn("SESSION_ID=a-window-v7", prompt)
        self.assertIn("PROJECT_STATE_VERSION=7", prompt)
        self.assertIn("A is a persistent identity", prompt)
        self.assertIn("DISPATCH", prompt)
        self.assertIn("DRAIN", prompt)
        self.assertIn("TERMINAL", prompt)
        self.assertIn("dynamic ephemeral workers", prompt)

    def test_worker_prompt_accepts_dynamic_worker_id(self):
        turn = self._master()
        turn.update({
            "turn_id": "worker-turn-789",
            "actor_kind": "WORKER",
            "actor_id": "worker-789",
            "session_id": "worker-789-session",
            "objective_sha256": "o" * 64,
            "payload": {"event": "ASSIGNMENT"},
        })
        prompt = render_actor_prompt_v3(turn)
        self.assertIn("WORKER_ID=worker-789", prompt)
        self.assertIn("OBJECTIVE_SHA256=" + "o" * 64, prompt)
        self.assertIn("must not dispatch", prompt)
        self.assertIn("must not publish production actions", prompt)
        self.assertIn("HANDOFF", prompt)

    def test_master_identity_other_than_a_is_rejected(self):
        turn = self._master()
        turn["actor_id"] = "B"
        with self.assertRaisesRegex(ValueError, "MASTER_IDENTITY_INVALID"):
            render_actor_prompt_v3(turn)

    def test_worker_identity_a_is_rejected(self):
        turn = self._master()
        turn.update({
            "actor_kind": "WORKER",
            "actor_id": "A",
            "session_id": "bad-worker",
            "objective_sha256": "o" * 64,
        })
        with self.assertRaisesRegex(ValueError, "WORKER_ID_INVALID"):
            render_actor_prompt_v3(turn)

    def test_missing_session_is_rejected(self):
        turn = self._master()
        turn.pop("session_id")
        with self.assertRaisesRegex(ValueError, "ACTOR_TURN_IDENTITY_MISSING"):
            render_actor_prompt_v3(turn)


if __name__ == "__main__":
    unittest.main()
