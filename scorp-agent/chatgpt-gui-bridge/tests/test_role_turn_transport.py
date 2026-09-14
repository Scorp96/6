import base64
import json
import unittest

import bridge_core
import gui_transport


def _token(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class RoleTurnTransportTests(unittest.TestCase):
    def test_extracts_bound_role_response(self):
        payload = {"kind": "HANDOFF", "worker_slot": "B", "status": "CHECKPOINT"}
        snapshot = f"SCORP_GUI_ROLE_V1::turn-001::{_token(payload)}"
        self.assertEqual(bridge_core.extract_role_response(snapshot, "turn-001"), payload)

    def test_wrong_turn_id_is_rejected(self):
        payload = {"kind": "HANDOFF", "worker_slot": "B", "status": "CHECKPOINT"}
        snapshot = f"SCORP_GUI_ROLE_V1::turn-other::{_token(payload)}"
        with self.assertRaisesRegex(ValueError, "ROLE_RESPONSE_MARKER_COUNT_0"):
            bridge_core.extract_role_response(snapshot, "turn-001")

    def test_malformed_base64_is_rejected(self):
        snapshot = "SCORP_GUI_ROLE_V1::turn-001::___"
        with self.assertRaisesRegex(ValueError, "ROLE_RESPONSE_INVALID"):
            bridge_core.extract_role_response(snapshot, "turn-001")

    def test_duplicate_conflicting_role_responses_are_rejected(self):
        one = _token({"kind": "HANDOFF", "worker_slot": "B", "status": "CHECKPOINT"})
        two = _token({"kind": "BLOCKER", "worker_slot": "B", "status": "BLOCKED"})
        snapshot = f"SCORP_GUI_ROLE_V1::turn-001::{one}\nSCORP_GUI_ROLE_V1::turn-001::{two}"
        with self.assertRaisesRegex(ValueError, "ROLE_RESPONSE_UNIQUE_COUNT_2"):
            bridge_core.extract_role_response(snapshot, "turn-001")

    def test_controller_prompt_binds_role_turn_identity(self):
        envelope = {
            "protocol_version": "scorp.gui-role-relay/turn-v1",
            "turn_id": "turn-a-001",
            "mission_id": "mission-1",
            "generation": 4,
            "from_role": "SYSTEM",
            "to_role": "A",
            "role_kind": "CONTROLLER",
            "root_objective_sha256": "a" * 64,
            "acceptance_sha256": "b" * 64,
            "payload": {"event": "WORKER_HANDOFF", "worker_slot": "B"},
        }
        prompt = gui_transport.render_role_prompt(envelope)
        self.assertIn("TURN_ID=turn-a-001", prompt)
        self.assertIn("MISSION_ID=mission-1", prompt)
        self.assertIn("ROLE=A", prompt)
        self.assertIn("SCORP_GUI_ROLE_V1", prompt)
        self.assertIn("DISPATCH", prompt)
        self.assertNotIn("SCORP_GUI_MUTATION_V2", prompt)

    def test_worker_prompt_forbids_dispatch_and_production_mutation(self):
        envelope = {
            "protocol_version": "scorp.gui-role-relay/turn-v1",
            "turn_id": "turn-b-001",
            "mission_id": "mission-1",
            "generation": 4,
            "from_role": "A",
            "to_role": "B",
            "role_kind": "WORKER",
            "root_objective_sha256": "a" * 64,
            "acceptance_sha256": "b" * 64,
            "objective_sha256": "c" * 64,
            "payload": {"objective": "Run bounded tests"},
        }
        prompt = gui_transport.render_role_prompt(envelope)
        self.assertIn("ROLE=B", prompt)
        self.assertIn("HANDOFF", prompt)
        self.assertIn("BLOCKER", prompt)
        self.assertIn("must not dispatch", prompt.lower())
        self.assertIn("must not publish", prompt.lower())


if __name__ == "__main__":
    unittest.main()
