from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


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

    def test_transport_exception_after_intent_fence_is_explicitly_blocked_ambiguous(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, engine, _ = self.make_runtime(root)

            def failed_submit(_intent):
                raise RuntimeError("CHROME_USE_EOF_DAEMON_BUSY")

            engine.submit = failed_submit
            try:
                result = BrowserAdapter(store, engine).submit_once("intent-ac03")
                self.assertEqual("BLOCKED_AMBIGUOUS", result["state"])
                self.assertEqual("SUBMIT_EXCEPTION_AMBIGUOUS", result["ambiguity_reason"])
                self.assertEqual("RuntimeError", __import__("json").loads(result["observation_json"])["error_type"])
            finally:
                store.close()

    def test_ambiguous_submit_retains_observed_conversation_url_for_read_only_reconcile(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, engine, _ = self.make_runtime(root)
            observed_url = "https://chatgpt.com/c/ac03-observed"
            reconcile_seen = []

            def ambiguous_submit(_intent):
                return {
                    "status": "SUBMITTED",
                    "conversation_url": observed_url,
                    # The missing remote identity makes the submit ambiguous,
                    # while the URL is still valuable read-only evidence.
                }

            def reconcile(intent):
                reconcile_seen.append(str(intent.get("conversation_url") or ""))
                return {"status": "AMBIGUOUS", "reason": "NO_RESPONSE_YET"}

            engine.submit = ambiguous_submit
            engine.reconcile = reconcile
            try:
                result = BrowserAdapter(store, engine).submit_once("intent-ac03")
                self.assertEqual("BLOCKED_AMBIGUOUS", result["state"])
                self.assertEqual(observed_url, result["conversation_url"])
                BrowserAdapter(store, engine).reconcile("intent-ac03")
                self.assertEqual([observed_url], reconcile_seen)
            finally:
                store.close()

    def test_sqlite_write_failure_before_intent_fence_fails_closed_without_browser_io(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter, BrowserAdapterError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, engine, _ = self.make_runtime(root)
            try:
                with patch.object(
                    store,
                    "begin_possible_submit",
                    side_effect=sqlite3.OperationalError("database or disk is full"),
                ):
                    with self.assertRaisesRegex(BrowserAdapterError, "SQLITE_WRITE_FAILED"):
                        BrowserAdapter(store, engine).submit_once("intent-ac03")
                self.assertEqual(0, engine.submit_count)
                self.assertEqual("PREPARED", store.get_intent("intent-ac03")["state"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
