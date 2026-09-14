from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.browser_adapter import BrowserAdapter
from master_a_dynamic_v4.operator_control import OperatorControlService
from master_a_dynamic_v4.runtime_protocol import parse_request
from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError


class _Engine:
    def __init__(self):
        self.auth_calls = 0
        self.submit_calls = 0

    def auth_state(self, channel):
        self.auth_calls += 1
        return {"status": "AUTHENTICATED"}

    def submit(self, intent):
        self.submit_calls += 1
        return {"status": "SUBMITTED", "conversation_url": "https://chatgpt.com/c/x", "remote_identity": "x"}

    def reconcile(self, intent):
        return {"status": "VERIFIED_NOT_SUBMITTED", "proof": "read-only-proof"}


class GenerationFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"required": []}
        )
        lease = self.store.acquire_daemon_lease("p1", "daemon")
        self.operator = OperatorControlService(self.store, daemon_epoch=lease["daemon_epoch"])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _pause(self):
        control = self.store.get_project_state("p1")
        lease = self.store.get_operator_control("p1")
        request = parse_request({
            "protocol_version": "scorp.runtime.command/1",
            "request_id": "pause",
            "command": "project.pause",
            "project_id": "p1",
            "expected_state_version": control["state_version"],
            "expected_daemon_epoch": 1,
            "payload": {},
        })
        return self.operator.execute(request)

    def test_paused_generation_blocks_browser_before_submit(self):
        control = self.store.get_operator_control("p1")
        self.store.prepare_intent(
            "p1", "intent-1", actor_id="worker", channel="worker/1", action_kind="CHATGPT_SUBMIT",
            payload={"prompt": "hello", "operator_generation": control["operator_generation"],
                     "objective_generation": control["objective_generation"]},
        )
        self._pause()
        engine = _Engine()
        result = BrowserAdapter(self.store, engine).submit_once("intent-1")
        self.assertEqual("BLOCKED_AMBIGUOUS", result["state"])
        self.assertIn("OPERATOR_GENERATION_FENCED", result["ambiguity_reason"])
        self.assertEqual(0, engine.submit_calls)

    def test_begin_possible_submit_rechecks_generation_in_transaction(self):
        control = self.store.get_operator_control("p1")
        self.store.prepare_intent(
            "p1", "intent-2", actor_id="worker", channel="worker/1", action_kind="LOCAL_EXECUTION",
            payload={"operator_generation": control["operator_generation"],
                     "objective_generation": control["objective_generation"]},
        )
        self._pause()
        with self.assertRaisesRegex(StoreInvariantError, "OPERATOR_GENERATION_FENCED"):
            self.store.begin_possible_submit("intent-2")
        self.assertEqual("PREPARED", self.store.get_intent("intent-2")["state"])


if __name__ == "__main__":
    unittest.main()
