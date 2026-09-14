import unittest

from gui_transport import render_actor_prompt_v3


class WorkerReusePromptBoundaryV3Tests(unittest.TestCase):
    def worker_turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "turn-worker-boundary",
            "project_id": "p",
            "state_version": 4,
            "actor_kind": "WORKER",
            "actor_id": "worker-boundary",
            "session_id": "session-boundary",
            "root_objective_sha256": "r" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "o" * 64,
            "payload": {"event": "ASSIGNMENT", "assignment": {"task": "current task"}},
        }

    def test_worker_prompt_declares_hard_assignment_boundary_for_reused_conversation(self):
        prompt = render_actor_prompt_v3(self.worker_turn())
        self.assertIn("WORKER_ASSIGNMENT_BOUNDARY=HARD", prompt)
        self.assertIn("prior turns in this conversation", prompt)
        self.assertIn("current PAYLOAD", prompt)
        self.assertIn("current binding fields", prompt)

    def test_master_prompt_does_not_claim_worker_assignment_boundary(self):
        turn = self.worker_turn()
        turn.update({
            "turn_id": "turn-master-boundary",
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-boundary",
        })
        turn.pop("objective_sha256")
        prompt = render_actor_prompt_v3(turn)
        self.assertNotIn("WORKER_ASSIGNMENT_BOUNDARY=HARD", prompt)


if __name__ == "__main__":
    unittest.main()
