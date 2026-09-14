import base64
import json
import unittest

from bridge_core import extract_actor_response_v3
from gui_transport import render_actor_prompt_v3


def token(value):
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class ActorResponseV3Tests(unittest.TestCase):
    def test_master_response_kinds_include_continue_and_drain(self):
        for kind in ("DISPATCH", "CONTINUE", "DRAIN", "WAIT", "TERMINAL"):
            with self.subTest(kind=kind):
                turn_id = "master-turn-1"
                snapshot = f"SCORP_GUI_ACTOR_V3::{turn_id}::{token({'kind': kind})}"
                self.assertEqual(kind, extract_actor_response_v3(snapshot, turn_id, "MASTER")["kind"])

    def test_worker_response_kinds_are_only_handoff_or_blocker(self):
        turn_id = "worker-turn-1"
        for kind in ("HANDOFF", "BLOCKER"):
            snapshot = f"SCORP_GUI_ACTOR_V3::{turn_id}::{token({'kind': kind})}"
            self.assertEqual(kind, extract_actor_response_v3(snapshot, turn_id, "WORKER")["kind"])
        bad = f"SCORP_GUI_ACTOR_V3::{turn_id}::{token({'kind': 'DISPATCH'})}"
        with self.assertRaisesRegex(ValueError, "ACTOR_RESPONSE_INVALID"):
            extract_actor_response_v3(bad, turn_id, "WORKER")

    def test_duplicate_identical_marker_is_allowed_but_conflicting_response_fails(self):
        turn_id = "master-turn-1"
        same = token({"kind": "WAIT"})
        snapshot = f"SCORP_GUI_ACTOR_V3::{turn_id}::{same}\nSCORP_GUI_ACTOR_V3::{turn_id}::{same}"
        self.assertEqual("WAIT", extract_actor_response_v3(snapshot, turn_id, "MASTER")["kind"])
        conflict = snapshot + f"\nSCORP_GUI_ACTOR_V3::{turn_id}::{token({'kind': 'DRAIN'})}"
        with self.assertRaisesRegex(ValueError, "ACTOR_RESPONSE_UNIQUE_COUNT_2"):
            extract_actor_response_v3(conflict, turn_id, "MASTER")

    def test_prompt_uses_v3_actor_marker_not_v2_role_marker(self):
        turn = {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "master-turn-1",
            "project_id": "p",
            "state_version": 1,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-1",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {"event": "RESUME_MASTER"},
        }
        prompt = render_actor_prompt_v3(turn)
        self.assertIn("SCORP_GUI_ACTOR_V3", prompt)
        self.assertNotIn("SCORP_GUI_ROLE_V1", prompt)


if __name__ == "__main__":
    unittest.main()
