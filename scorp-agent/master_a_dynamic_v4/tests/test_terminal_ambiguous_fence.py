from __future__ import annotations

import pathlib
import tempfile
import unittest


class NoIoEngine:
    def __init__(self):
        self.submit_count = 0
        self.reconcile_count = 0
    def auth_state(self, _channel):
        return {"status": "AUTHENTICATED"}
    def submit(self, _intent):
        self.submit_count += 1
        raise AssertionError("FENCED_INTENT_MUST_NOT_SUBMIT")
    def reconcile(self, _intent):
        self.reconcile_count += 1
        raise AssertionError("FENCED_INTENT_MUST_NOT_RECONCILE")


class TerminalAmbiguousFenceTests(unittest.TestCase):
    def test_block_intent_cannot_downgrade_captured_response(self):
        from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            store.create_contract(
                "p",
                root_contract={"objective": "guard captured intent"},
                acceptance_contract={"required": []},
            )
            store.prepare_intent(
                "p", "intent-captured", actor_id="worker", channel="worker/1",
                action_kind="CHATGPT_WORKER_SUBMIT", payload={"prompt": "x"},
            )
            store.begin_possible_submit("intent-captured")
            store.capture_response(
                "intent-captured",
                response={"work_result_version": "1"},
                conversation_url="https://chatgpt.com/c/captured",
                remote_identity="worker-1",
                observation={"source": "test"},
            )
            with self.assertRaisesRegex(StoreInvariantError, "INTENT_BLOCK_STATE_INVALID"):
                store.block_intent(
                    "intent-captured",
                    reason="LATE_ERROR",
                    observation={"source": "late-caller"},
                )
            current = store.get_intent("intent-captured")
            self.assertEqual("RESPONSE_CAPTURED", current["state"])
            self.assertEqual("PENDING_CLEANUP", store.get_outbox_for_intent("intent-captured")["state"])
            store.close()

    def test_terminal_fence_preserves_evidence_and_removes_side_effect_authority(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter, BrowserAdapterError
        from master_a_dynamic_v4.models import IntentState
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            store.create_contract(
                "p",
                root_contract={"objective": "terminal ambiguous fence"},
                acceptance_contract={"required": ["fence"]},
            )
            store.prepare_intent(
                "p", "intent-old", actor_id="worker-old", channel="worker/worker-slot-1",
                action_kind="CHATGPT_WORKER_SUBMIT", payload={"prompt_sha256": "a" * 64},
            )
            store.begin_possible_submit("intent-old")
            store.block_intent(
                "intent-old", reason="SUBMIT_EXCEPTION_AMBIGUOUS",
                observation={"status": "SUBMITTED", "conversation_url": "https://chatgpt.com/c/old"},
            )
            before = store.get_intent("intent-old")
            self.assertEqual("BLOCKED_AMBIGUOUS", before["state"])
            fenced = store.fence_ambiguous_intent(
                "intent-old", reason="READ_ONLY_RECONCILIATION_EXHAUSTED",
                observation={"slot1_probe": "TIMED_OUT", "slot2_probe": "TIMED_OUT"},
            )
            self.assertEqual(IntentState.FENCED_AMBIGUOUS.value, fenced["state"])
            self.assertEqual(before["conversation_url"], fenced["conversation_url"])
            self.assertEqual("COMPLETED", store.get_outbox_for_intent("intent-old")["state"])
            self.assertEqual([], store.pending_intents())
            snapshot = store.activation_snapshot("p", daemon_epoch=1)
            self.assertEqual(0, snapshot.ambiguous_intents)
            engine = NoIoEngine()
            adapter = BrowserAdapter(store, engine)
            with self.assertRaisesRegex(BrowserAdapterError, "INTENT_FENCED_AMBIGUOUS"):
                adapter.submit_once("intent-old")
            result = adapter.reconcile("intent-old")
            self.assertEqual(IntentState.FENCED_AMBIGUOUS.value, result["state"])
            self.assertEqual(0, engine.submit_count)
            self.assertEqual(0, engine.reconcile_count)
            store.close()


if __name__ == "__main__":
    unittest.main()
