from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.master_reasoning import MasterReasoningCoordinator
from master_a_dynamic_v4.state_store import StateStore


class _Gateway:
    def __init__(self, store):
        self.store = store
        self.project_id = "p"

    def submit_intent(self, _intent_id):
        raise AssertionError("submit is not part of this contract test")


class _Controller:
    def apply_plan(self, _plan):
        return {"status": "ADMITTED"}


class R2MasterReasoningTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "state.sqlite3", [self.root])
        self.store.create_contract(
            "p",
            root_contract={"objective": "r2"},
            acceptance_contract={"required": []},
        )
        self.coordinator = MasterReasoningCoordinator(
            _Gateway(self.store),
            _Controller(),
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_bootstrap_reasoning_persists_explicit_null_transport_binding(self):
        prepared = self.coordinator._prepare()
        payload = json.loads(prepared["payload_json"])
        self.assertIn("transport_binding", payload)
        self.assertIsNone(payload["transport_binding"])

    def test_bound_master_is_in_semantic_snapshot_and_prepared_transport(self):
        url = "https://chatgpt.com/c/master-r2"
        persisted = self.store.rebind_browser(
            "p",
            "master",
            actor_id="A",
            conversation_url=url,
            predecessor_url=None,
            reason="INITIAL_BINDING",
            evidence={"source": "unit-test", "session": "scorp-p0-conv-r2"},
        )

        snapshot = self.coordinator.semantic_snapshot()
        browser = snapshot["master_browser_binding"]
        self.assertEqual(url, browser["conversation_url"])
        self.assertEqual("A", browser["actor_id"])
        self.assertEqual(0, int(browser["generation"]))

        prepared = self.coordinator._prepare()
        payload = json.loads(prepared["payload_json"])
        self.assertEqual(
            {
                "channel": "master",
                "actor_id": "A",
                "conversation_url": url,
                "generation": int(persisted["generation"]),
            },
            payload["transport_binding"],
        )

    def test_binding_rotation_changes_reasoning_intent_identity(self):
        first_url = "https://chatgpt.com/c/master-r2-a"
        second_url = "https://chatgpt.com/c/master-r2-b"
        self.store.rebind_browser(
            "p",
            "master",
            actor_id="A",
            conversation_url=first_url,
            predecessor_url=None,
            reason="INITIAL_BINDING",
            evidence={"source": "unit-test"},
        )
        _first_binding, _first_prompt, first_intent = self.coordinator._binding_and_prompt()

        self.store.rebind_browser(
            "p",
            "master",
            actor_id="A",
            conversation_url=second_url,
            predecessor_url=first_url,
            reason="EXPLICIT_ROTATION",
            evidence={"source": "unit-test"},
        )
        _second_binding, _second_prompt, second_intent = self.coordinator._binding_and_prompt()

        self.assertNotEqual(first_intent, second_intent)


if __name__ == "__main__":
    unittest.main()
