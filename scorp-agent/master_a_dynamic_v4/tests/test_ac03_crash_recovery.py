from __future__ import annotations

import pathlib
import tempfile
import unittest


class FakeEngine:
    def __init__(self, store, intent_id, *, auth="AUTHENTICATED"):
        self.store = store
        self.intent_id = intent_id
        self.auth = auth
        self.submit_count = 0
        self.reconcile_result = {"status": "AMBIGUOUS", "reason": "NO_PROOF"}

    def auth_state(self, channel):
        return {"status": self.auth, "channel": channel}

    def submit(self, intent):
        self.assert_may_have_submitted()
        self.submit_count += 1
        return {
            "status": "SUBMITTED",
            "conversation_url": "https://chatgpt.com/c/ac03",
            "remote_identity": "remote-ac03",
        }

    def assert_may_have_submitted(self):
        state = self.store.get_intent(self.intent_id)["state"]
        if state != "MAY_HAVE_SUBMITTED":
            raise AssertionError(f"browser I/O observed state={state}")

    def reconcile(self, intent):
        return dict(self.reconcile_result)


class CrashRecoveryTests(unittest.TestCase):
    def make_runtime(self, root: pathlib.Path, *, failpoint=None):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter
        from master_a_dynamic_v4.state_store import StateStore

        store = StateStore(root / "state.sqlite3", allowed_roots=[root])
        store.create_contract("project-ac03", root_contract={"objective": "crash recovery"}, acceptance_contract={"required": ["AC03"]})
        store.prepare_intent("project-ac03", "intent-ac03", actor_id="A", channel="master", action_kind="CHATGPT_SUBMIT", payload={"prompt_sha256": "a" * 64})
        engine = FakeEngine(store, "intent-ac03")
        return store, engine, BrowserAdapter(store, engine, failpoint=failpoint)

    def test_crash_after_may_have_submitted_occurs_before_browser_io_and_requires_positive_no_submit_proof(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter, InjectedCrash

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, engine, adapter = self.make_runtime(root, failpoint="after_may_have_submitted")
            try:
                with self.assertRaises(InjectedCrash):
                    adapter.submit_once("intent-ac03")
                self.assertEqual(0, engine.submit_count)
                self.assertEqual("MAY_HAVE_SUBMITTED", store.get_intent("intent-ac03")["state"])
                engine.reconcile_result = {"status": "VERIFIED_NOT_SUBMITTED", "proof": "composer_unchanged_and_no_marker"}
                recovered = BrowserAdapter(store, engine).reconcile("intent-ac03")
                self.assertEqual("VERIFIED_NOT_SUBMITTED", recovered["state"])
                submitted = BrowserAdapter(store, engine).submit_once("intent-ac03")
                self.assertEqual("CONFIRMED_SUBMITTED", submitted["state"])
                self.assertEqual(1, engine.submit_count)
            finally:
                store.close()

    def test_crash_after_remote_submit_reconciles_without_duplicate_submit(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter, InjectedCrash, ReconcileRequired

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, engine, adapter = self.make_runtime(root, failpoint="after_remote_submit")
            try:
                with self.assertRaises(InjectedCrash):
                    adapter.submit_once("intent-ac03")
                self.assertEqual(1, engine.submit_count)
                self.assertEqual("MAY_HAVE_SUBMITTED", store.get_intent("intent-ac03")["state"])
                with self.assertRaises(ReconcileRequired):
                    BrowserAdapter(store, engine).submit_once("intent-ac03")
                engine.reconcile_result = {
                    "status": "CONFIRMED_SUBMITTED",
                    "conversation_url": "https://chatgpt.com/c/ac03",
                    "remote_identity": "remote-ac03",
                }
                recovered = BrowserAdapter(store, engine).reconcile("intent-ac03")
                self.assertEqual("CONFIRMED_SUBMITTED", recovered["state"])
                self.assertEqual(1, engine.submit_count)
            finally:
                store.close()

    def test_response_capture_survives_crash_before_terminal_outbox_cleanup(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter, InjectedCrash
        from master_a_dynamic_v4.recovery import recover_pending_intents

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, engine, adapter = self.make_runtime(root)
            try:
                adapter.submit_once("intent-ac03")
                engine.reconcile_result = {
                    "status": "RESPONSE_CAPTURED",
                    "conversation_url": "https://chatgpt.com/c/ac03",
                    "remote_identity": "remote-ac03",
                    "response": {"kind": "HANDOFF", "value": 7},
                }
                crashing = BrowserAdapter(store, engine, failpoint="after_response_capture")
                with self.assertRaises(InjectedCrash):
                    crashing.reconcile("intent-ac03")
                self.assertEqual("RESPONSE_CAPTURED", store.get_intent("intent-ac03")["state"])
                self.assertEqual("PENDING_CLEANUP", store.get_outbox_for_intent("intent-ac03")["state"])
                outcomes = recover_pending_intents(BrowserAdapter(store, engine))
                self.assertEqual([("intent-ac03", "FINALIZED")], outcomes)
                self.assertEqual("COMPLETED", store.get_outbox_for_intent("intent-ac03")["state"])
                self.assertEqual(1, engine.submit_count)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
