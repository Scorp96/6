from __future__ import annotations

import pathlib
import tempfile
import unittest


class FakeEngine:
    def __init__(self, store, intent_id):
        self.store = store
        self.intent_id = intent_id
        self.submit_count = 0
        self.reconcile_result = {"status": "AMBIGUOUS", "reason": "NO_PROOF"}

    def auth_state(self, channel):
        return {"status": "AUTHENTICATED", "channel": channel}

    def submit(self, intent):
        self.submit_count += 1
        return {"status": "SUBMITTED", "conversation_url": "https://chatgpt.com/c/ac07", "remote_identity": "remote-ac07"}

    def reconcile(self, intent):
        return dict(self.reconcile_result)


class BrowserRebindTests(unittest.TestCase):
    def make_runtime(self, root):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter
        from master_a_dynamic_v4.state_store import StateStore

        store = StateStore(root / "state.sqlite3", allowed_roots=[root])
        store.create_contract("project-ac07", root_contract={"objective": "rebind"}, acceptance_contract={"required": ["AC07"]})
        store.prepare_intent("project-ac07", "intent-ac07", actor_id="A", channel="master", action_kind="CHATGPT_SUBMIT", payload={"prompt_sha256": "7" * 64})
        engine = FakeEngine(store, "intent-ac07")
        return store, engine, BrowserAdapter(store, engine)

    def test_rebind_requires_predecessor_reason_and_evidence_and_keeps_master_a(self):
        from master_a_dynamic_v4.browser_adapter import BrowserBindingError

        with tempfile.TemporaryDirectory() as td:
            store, _, adapter = self.make_runtime(pathlib.Path(td))
            try:
                first = adapter.rebind("project-ac07", "master", actor_id="A", conversation_url="https://chatgpt.com/c/one", predecessor_url=None, reason="INITIAL_BIND", evidence={"source": "verified_url"})
                self.assertEqual(0, first["generation"])
                with self.assertRaises(BrowserBindingError):
                    adapter.rebind("project-ac07", "master", actor_id="A", conversation_url="https://chatgpt.com/c/two", predecessor_url=None, reason="", evidence={})
                second = adapter.rebind("project-ac07", "master", actor_id="A", conversation_url="https://chatgpt.com/c/two", predecessor_url="https://chatgpt.com/c/one", reason="UNRECOVERABLE_CHANNEL", evidence={"receipt": "e-1"})
                self.assertEqual("A", second["actor_id"])
                self.assertEqual(1, second["generation"])
                self.assertEqual("https://chatgpt.com/c/one", second["predecessor_url"])
            finally:
                store.close()

    def test_missing_url_or_ambiguous_reconcile_never_resubmits(self):
        with tempfile.TemporaryDirectory() as td:
            store, engine, adapter = self.make_runtime(pathlib.Path(td))
            try:
                engine.submit = lambda intent: {"status": "SUBMITTED", "conversation_url": ""}
                row = adapter.submit_once("intent-ac07")
                self.assertEqual("BLOCKED_AMBIGUOUS", row["state"])
                self.assertEqual(0, engine.submit_count)
                engine.reconcile_result = {"status": "AMBIGUOUS", "reason": "NO_UNIQUE_URL"}
                row = adapter.reconcile("intent-ac07")
                self.assertEqual("BLOCKED_AMBIGUOUS", row["state"])
                self.assertEqual(0, engine.submit_count)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
