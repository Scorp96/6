import json
import tempfile
import unittest
from pathlib import Path

from bridge_worker import BridgeRuntime


ROOT_HASH = "a" * 64
ACCEPT_HASH = "b" * 64


def write_role_turn(bridge: Path):
    outbox = bridge / "role-relay-outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    turn = {
        "protocol_version": "scorp.gui-role-relay/turn-v1",
        "turn_id": "turn-a-main-001",
        "mission_id": "mission-main-1",
        "generation": 1,
        "from_role": "SYSTEM",
        "to_role": "A",
        "role_kind": "CONTROLLER",
        "root_objective_sha256": ROOT_HASH,
        "acceptance_sha256": ACCEPT_HASH,
        "role_turn_budget_seconds": 1500,
        "handoff_reserve_seconds": 300,
        "payload": {"event": "MISSION_BOOTSTRAP", "objective": "dispatch bounded work"},
    }
    (outbox / "turn-a-main-001.json").write_text(json.dumps(turn), encoding="utf-8")


class EmptyGitHub:
    def list_comments(self, issue_number):
        return []


class BridgeRoleRelayRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_legacy_work_runs_one_role_turn_instead_of_idle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orch = root / "orch"; state = root / "state"; bridge = root / "bridge"
            orch.mkdir(); state.mkdir(); bridge.mkdir()
            (orch / "registry.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
            (orch / "continuation-outbox").mkdir()
            write_role_turn(bridge)
            legacy_calls = []
            role_calls = []

            async def legacy_gui(*args, **kwargs):
                legacy_calls.append((args, kwargs))
                raise AssertionError("legacy GUI must not run")

            async def role_gui(prompt, turn_id, *, conversation_url=None, timeout_seconds=1800):
                role_calls.append((turn_id, conversation_url, timeout_seconds))
                return ({"kind": "TERMINAL", "state": "DONE", "reason": "synthetic"},
                        "snapshot", "https://chatgpt.com/c/controller-a-real")

            runtime = BridgeRuntime(orch, state, bridge, EmptyGitHub(), legacy_gui, "Scorp96",
                                    role_gui_turn=role_gui)
            result = await runtime.run_once()
            self.assertEqual(result["status"], "ROUTED")
            self.assertEqual(result["role"], "A")
            self.assertEqual(len(role_calls), 1)
            self.assertEqual(len(legacy_calls), 0)
            self.assertEqual(role_calls[0][1], None)
            self.assertEqual(role_calls[0][2], 1800)

    async def test_saved_role_chat_is_reused_by_bridge(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orch = root / "orch"; state = root / "state"; bridge = root / "bridge"
            orch.mkdir(); state.mkdir(); bridge.mkdir()
            (orch / "registry.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
            (orch / "continuation-outbox").mkdir()
            write_role_turn(bridge)
            seen = []

            async def legacy_gui(*args, **kwargs):
                raise AssertionError("legacy GUI must not run")

            async def role_gui(prompt, turn_id, *, conversation_url=None, timeout_seconds=1800):
                seen.append(conversation_url)
                return ({"kind": "TERMINAL", "state": "DONE"}, "snapshot",
                        "https://chatgpt.com/c/controller-a-real")

            runtime = BridgeRuntime(orch, state, bridge, EmptyGitHub(), legacy_gui, "Scorp96",
                                    role_gui_turn=role_gui)
            runtime.conversations.record_role("mission-main-1", "A", "seed-a", "https://chatgpt.com/c/controller-a-real")
            await runtime.run_once()
            self.assertEqual(seen, ["https://chatgpt.com/c/controller-a-real"])

    async def test_legacy_continuation_has_one_poll_priority_and_relay_does_not_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orch = root / "orch"; state = root / "state"; bridge = root / "bridge"
            orch.mkdir(); state.mkdir(); bridge.mkdir()
            (orch / "continuation-outbox").mkdir()
            write_role_turn(bridge)
            request = {
                "request_id":"req-legacy-1","protocol_version":"scorp.orchestrator/continuation-request-v1",
                "task_id":"task-legacy","project_id":"proj","continuation_generation":1,"registry_sequence":1,
                "previous_action_id":None,"previous_result_sha256":None,"issue_number":91,"publication_state":"PUBLISHED",
                "safety_class":"standard","expires_at":"2099-01-01T00:00:00Z","created_at":"2026-01-01T00:00:00Z"
            }
            task = {
                "task_id":"task-legacy","project_id":"proj","state":"WAITING","waiting_reason":"GPT_CONTINUATION_REQUIRED",
                "continuation_request_id":"req-legacy-1","generation":1,"priority":10,"safety_class":"standard",
                "authorization_requirement":None,"last_result":{}
            }
            (orch / "registry.json").write_text(json.dumps({"tasks":[task]}), encoding="utf-8")
            (orch / "continuation-outbox" / "req-legacy-1.json").write_text(json.dumps(request), encoding="utf-8")
            legacy_calls = []
            role_calls = []

            async def legacy_gui(prompt, request_id, *, conversation_url=None):
                legacy_calls.append(request_id)
                raise RuntimeError("LEGACY_SENTINEL")

            async def role_gui(*args, **kwargs):
                role_calls.append((args, kwargs))
                raise AssertionError("role GUI must not run in same poll")

            runtime = BridgeRuntime(orch, state, bridge, EmptyGitHub(), legacy_gui, "Scorp96",
                                    role_gui_turn=role_gui)
            with self.assertRaisesRegex(RuntimeError, "LEGACY_SENTINEL"):
                await runtime.run_once()
            self.assertEqual(legacy_calls, ["req-legacy-1"])
            self.assertEqual(role_calls, [])


if __name__ == "__main__":
    unittest.main()
