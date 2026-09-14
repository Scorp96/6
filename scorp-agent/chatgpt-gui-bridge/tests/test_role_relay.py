import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from bridge_worker import ConversationRegistry
from role_relay import RoleRelayRuntime


ROOT_HASH = "a" * 64
ACCEPT_HASH = "b" * 64
OBJ_HASH = "c" * 64


def bootstrap_turn():
    return {
        "protocol_version": "scorp.gui-role-relay/turn-v1",
        "turn_id": "turn-a-001",
        "mission_id": "mission-live-1",
        "generation": 1,
        "from_role": "SYSTEM",
        "to_role": "A",
        "role_kind": "CONTROLLER",
        "root_objective_sha256": ROOT_HASH,
        "acceptance_sha256": ACCEPT_HASH,
        "payload": {"event": "MISSION_BOOTSTRAP", "objective": "Build the approved relay canary"},
    }


class ScriptedGui:
    def __init__(self):
        self.calls = []

    async def __call__(self, prompt, turn_id, *, conversation_url=None, timeout_seconds=1800):
        self.calls.append((turn_id, conversation_url, timeout_seconds, prompt))
        if turn_id == "turn-a-001":
            return ({
                "kind": "DISPATCH",
                "assignments": [{
                    "worker_slot": "B",
                    "objective": "Run bounded synthetic work",
                    "objective_sha256": OBJ_HASH,
                    "resource_scope": ["synthetic/*"],
                    "success_criteria": ["return handoff"],
                }],
            }, "snapshot-a1", "https://chatgpt.com/c/controller-a-111")
        if turn_id.startswith("relay-b-"):
            return ({
                "kind": "HANDOFF",
                "worker_slot": "B",
                "status": "CHECKPOINT",
                "completed": ["synthetic work"],
                "remaining": [],
                "evidence": ["synthetic-pass"],
                "safe_resume_point": "done",
            }, "snapshot-b1", "https://chatgpt.com/c/worker-b-222")
        if turn_id.startswith("relay-a-"):
            return ({"kind": "TERMINAL", "state": "DONE", "reason": "handoff accepted"},
                    "snapshot-a2", "https://chatgpt.com/c/controller-a-111")
        raise AssertionError(turn_id)


class RoleRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_a_b_a_alternation_persists_distinct_chats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outbox = root / "role-relay-outbox"
            outbox.mkdir()
            (outbox / "turn-a-001.json").write_text(json.dumps(bootstrap_turn()), encoding="utf-8")
            conversations = ConversationRegistry(root / "conversation-registry.json")
            gui = ScriptedGui()
            runtime = RoleRelayRuntime(root, conversations, gui)

            r1 = await runtime.run_once()
            self.assertEqual(r1["status"], "ROUTED")
            self.assertEqual(conversations.get_role_url("mission-live-1", "A"), "https://chatgpt.com/c/controller-a-111")

            r2 = await runtime.run_once()
            self.assertEqual(r2["status"], "ROUTED")
            self.assertEqual(conversations.get_role_url("mission-live-1", "B"), "https://chatgpt.com/c/worker-b-222")
            self.assertNotEqual(conversations.get_role_url("mission-live-1", "A"), conversations.get_role_url("mission-live-1", "B"))

            r3 = await runtime.run_once()
            self.assertEqual(r3["status"], "ROUTED")
            self.assertEqual(r3["response"]["kind"], "TERMINAL")
            self.assertEqual(gui.calls[2][1], "https://chatgpt.com/c/controller-a-111")
            self.assertEqual(gui.calls[0][2], 1800)
            self.assertEqual(gui.calls[1][2], 1800)

            ledger = json.loads((root / "role-relay-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(len([v for v in ledger.values() if v["state"] == "ROUTED"]), 3)

    async def test_worker_cannot_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outbox = root / "role-relay-outbox"
            outbox.mkdir()
            turn = bootstrap_turn()
            turn.update({"turn_id": "turn-b-bad", "from_role": "A", "to_role": "B", "role_kind": "WORKER", "objective_sha256": OBJ_HASH})
            (outbox / "turn-b-bad.json").write_text(json.dumps(turn), encoding="utf-8")
            conversations = ConversationRegistry(root / "conversation-registry.json")

            async def bad_gui(prompt, turn_id, *, conversation_url=None, timeout_seconds=1800):
                return ({"kind": "DISPATCH", "assignments": []}, "snapshot", "https://chatgpt.com/c/worker-b-222")

            runtime = RoleRelayRuntime(root, conversations, bad_gui)
            with self.assertRaisesRegex(ValueError, "WORKER_RESPONSE_KIND_INVALID"):
                await runtime.run_once()

    async def test_routed_turn_is_not_executed_twice(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outbox = root / "role-relay-outbox"
            outbox.mkdir()
            (outbox / "turn-a-001.json").write_text(json.dumps(bootstrap_turn()), encoding="utf-8")
            conversations = ConversationRegistry(root / "conversation-registry.json")
            gui = ScriptedGui()
            runtime = RoleRelayRuntime(root, conversations, gui)
            await runtime.run_once()
            first_calls = len(gui.calls)
            # Remove generated child so only the already-routed parent remains.
            for path in outbox.glob("relay-b-*.json"):
                path.unlink()
            result = await runtime.run_once()
            self.assertEqual(result["status"], "IDLE")
            self.assertEqual(len(gui.calls), first_calls)


if __name__ == "__main__":
    unittest.main()
