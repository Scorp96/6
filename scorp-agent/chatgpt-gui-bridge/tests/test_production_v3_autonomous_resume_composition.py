import base64
import json
import pathlib
import tempfile
import unittest

from master_window_lease_v3 import MasterWindowLeaseStore
from production_v3_runtime import ProductionV3Runtime
from project_bootstrap_v3 import ProjectBootstrapV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3


def token(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class ScriptedActorDriver:
    def __init__(self):
        self.responses = [
            {"kind": "CONTINUE", "state_patch": {"current_phase": "CORE", "next_exact_action": "continue in A1"}},
            {"kind": "DRAIN", "state_patch": {"current_phase": "HANDOFF", "next_exact_action": "resume in A2"}},
            {"kind": "WAIT"},
        ]
        self.calls = []
        self.snapshots = {}
        self.url = "https://chatgpt.com/c/round2-production-runtime"

    async def submit_prompt(self, *, prompt, turn_id, actor_kind, conversation_url):
        if not self.responses:
            raise AssertionError("UNEXPECTED_DRIVER_SUBMIT")
        if actor_kind != "MASTER":
            raise AssertionError("UNEXPECTED_NON_MASTER_SUBMIT")
        if conversation_url not in (None, self.url):
            raise AssertionError("MASTER_CONVERSATION_CHANGED")
        if ("TURN_ID=" + turn_id) not in prompt:
            raise AssertionError("TURN_ID_PROMPT_MISMATCH")
        response = self.responses.pop(0)
        snapshot = self.url + "\n" + "SCORP_GUI_ACTOR_V3::" + turn_id + "::" + token(response)
        self.snapshots[self.url] = snapshot
        self.calls.append({"turn_id": turn_id, "conversation_url": conversation_url})
        return snapshot

    async def snapshot_conversation(self, conversation_url, **kwargs):
        return self.snapshots[conversation_url]

    async def discover_turn_conversations(self, turn_id):
        return []


class ProductionV3AutonomousResumeCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_composes_drain_watchdog_resume_into_new_a_without_user_activation(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "active"
            ProjectBootstrapV3(root).create(
                project_id="round2-production-runtime",
                root_objective="prove autonomous D resume through production runtime",
                acceptance_criteria="A1 drains, D emits resume, A2 starts without user activation",
            )
            state = ProjectStateStore(root / "project-state.json")
            initial = state.load()
            goal_hash = initial["goal_contract_sha256"]
            acceptance_hash = initial["acceptance_sha256"]
            driver = ScriptedActorDriver()
            runtime = ProductionV3Runtime(root, driver=driver, max_inflight=1)

            for _ in range(10):
                await runtime.run_once()
                if len(driver.calls) >= 3:
                    break
            self.assertEqual(3, len(driver.calls), "A2 was not autonomously submitted")

            outbox = root / "master-worker-v3-outbox"
            turns = {}
            for p in outbox.glob("*.json"):
                row = json.loads(p.read_text(encoding="utf-8-sig"))
                turns[row["turn_id"]] = row
            submitted = [turns[c["turn_id"]] for c in driver.calls]
            s1, s2, s3 = [row["session_id"] for row in submitted]
            self.assertEqual(s1, s2)
            self.assertNotEqual(s1, s3)
            self.assertTrue(s1.startswith("a-window-v0-"))
            self.assertTrue(s3.startswith("a-window-v2-"))

            self.assertIsNone(driver.calls[0]["conversation_url"])
            self.assertEqual(driver.url, driver.calls[1]["conversation_url"])
            self.assertEqual(driver.url, driver.calls[2]["conversation_url"])
            sessions = SessionRegistryV3(root / "sessions-v3.json")
            self.assertEqual(driver.url, sessions.get_master_conversation_url("round2-production-runtime"))

            current = state.load()
            self.assertEqual("ACTIVE", current["status"])
            self.assertEqual(2, current["state_version"])
            self.assertEqual(goal_hash, current["goal_contract_sha256"])
            self.assertEqual(acceptance_hash, current["acceptance_sha256"])
            self.assertEqual("A", current["master_identity"])

            lease = MasterWindowLeaseStore(root / "master-window-lease.json").load("round2-production-runtime")
            self.assertEqual("ACTIVE", lease["status"])
            self.assertEqual(s3, lease["session_id"])
            self.assertEqual([], driver.responses)


if __name__ == "__main__":
    unittest.main()