"""P0 SQLite proof: distinct Worker slots may not claim one conversation URL."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError


class WorkerConversationCollisionTests(unittest.TestCase):
    URL1 = "https://chatgpt.com/c/isolated-worker-one"
    URL2 = "https://chatgpt.com/c/isolated-worker-two"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.store = StateStore(root / "state.sqlite3", allowed_roots=[root])
        self.addCleanup(self.store.close)
        self.project = "worker-url-collision"
        self.store.create_contract(
            self.project,
            root_contract={"objective": "distinct chat sessions"},
            acceptance_contract={"required": ["AC-IDENTITY"]},
        )
        self.one = self.prepare("worker/worker-slot-1", "worker-1")
        self.two = self.prepare("worker/worker-slot-2", "worker-2")

    def prepare(self, channel: str, actor_id: str):
        intent_id = f"test-{self.project}-{actor_id}"
        self.store.prepare_intent(
            self.project, intent_id,
            actor_id=actor_id, channel=channel,
            action_kind="CHATGPT_WORKER_SUBMIT",
            payload={"prompt": "fixture-only"},
        )
        self.store.begin_possible_submit(intent_id)
        return intent_id

    def capture(self, intent_id, url):
        return self.store.capture_response(
            intent_id,
            response={"work_result_version": "1", "status": "COMPLETE"},
            conversation_url=url,
            remote_identity="fixture-turn",
            observation={"status": "RESPONSE_CAPTURED"},
        )

    def test_second_slot_same_url_is_rejected_before_durable_result(self):
        self.capture(self.one, self.URL1)
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_CONVERSATION_COLLISION"):
            self.capture(self.two, self.URL1)
        self.assertEqual("MAY_HAVE_SUBMITTED", self.store.get_intent(self.two)["state"])
        self.assertEqual("SUBMITTING", self.store.get_outbox_for_intent(self.two)["state"])

    def test_distinct_slot_url_captures_are_allowed(self):
        self.capture(self.one, self.URL1)
        second = self.capture(self.two, self.URL2)
        self.assertEqual(self.URL2, second["conversation_url"])
        self.assertEqual("RESPONSE_CAPTURED", second["state"])

    def test_second_slot_duplicate_submit_confirmation_is_rejected(self):
        self.store.confirm_submitted(
            self.one, conversation_url=self.URL1,
            remote_identity="fixture-one", observation={"status": "SUBMITTED"},
        )
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_CONVERSATION_COLLISION"):
            self.store.confirm_submitted(
                self.two, conversation_url=self.URL1,
                remote_identity="fixture-two", observation={"status": "SUBMITTED"},
            )
        self.assertEqual("MAY_HAVE_SUBMITTED", self.store.get_intent(self.two)["state"])

    def test_worker_binding_rebind_to_another_slots_url_rejected(self):
        self.store.rebind_browser(
            self.project, "worker/worker-slot-1", actor_id="worker-1",
            conversation_url=self.URL1, predecessor_url=None,
            reason="INITIAL", evidence={"kind": "isolated"},
        )
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_CONVERSATION_COLLISION"):
            self.store.rebind_browser(
                self.project, "worker/worker-slot-2", actor_id="worker-2",
                conversation_url=self.URL1, predecessor_url=None,
                reason="INITIAL", evidence={"kind": "isolated"},
            )
        self.assertIsNone(self.store.get_browser_binding(self.project, "worker/worker-slot-2"))

    def test_same_slot_repeated_bind_of_same_url_remains_idempotent(self):
        kwargs = dict(
            actor_id="worker-1", conversation_url=self.URL1,
            predecessor_url=None, reason="INITIAL", evidence={"kind": "isolated"},
        )
        self.store.rebind_browser(self.project, "worker/worker-slot-1", **kwargs)
        old = self.store.get_browser_binding(self.project, "worker/worker-slot-1")
        again = self.store.rebind_browser(self.project, "worker/worker-slot-1", **kwargs)
        self.assertEqual(old["generation"], again["generation"])

    def test_duplicate_url_in_browser_binding_blocks_intent_capture(self):
        self.store.rebind_browser(
            self.project, "worker/worker-slot-1", actor_id="worker-1",
            conversation_url=self.URL1, predecessor_url=None,
            reason="INITIAL", evidence={"kind": "isolated"},
        )
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_CONVERSATION_COLLISION"):
            self.capture(self.two, self.URL1)

    def test_different_project_may_have_its_own_same_url_history(self):
        other = "other-project"
        self.store.create_contract(
            other, root_contract={"objective": "other"},
            acceptance_contract={"required": ["AC-OTHER"]},
        )
        self.capture(self.one, self.URL1)
        third = "separate-project-intent"
        self.store.prepare_intent(
            other, third, actor_id="other-worker",
            channel="worker/worker-slot-2",
            action_kind="CHATGPT_WORKER_SUBMIT",
            payload={"prompt": "other isolated"},
        )
        self.store.begin_possible_submit(third)
        self.store.capture_response(
            third, response={"status": "COMPLETE"},
            conversation_url=self.URL1, remote_identity="fixture-other",
            observation={"status": "RESPONSE_CAPTURED"},
        )
        self.assertEqual("RESPONSE_CAPTURED", self.store.get_intent(third)["state"])

    def test_master_and_worker_legacy_channel_not_treated_as_two_workers(self):
        self.capture(self.one, self.URL1)
        master = "master-fixture"
        self.store.prepare_intent(
            self.project, master, actor_id="A",
            channel="master", action_kind="CHATGPT_MASTER_SUBMIT",
            payload={"prompt": "master isolated"},
        )
        self.store.begin_possible_submit(master)
        r = self.store.capture_response(
            master, response={"status": "DONE"},
            conversation_url=self.URL1, remote_identity="master-turn",
            observation={"status": "RESPONSE_CAPTURED"},
        )
        self.assertEqual("RESPONSE_CAPTURED", r["state"])


if __name__ == "__main__":
    unittest.main()
