import json
import pathlib
import tempfile
import unittest

sys_path = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(sys_path))

from v4_browser_engine import build_v4_browser_engine


class FakeDriver:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.submits = []
        self.reconciles = []
        self.unpromoted = []
        self.not_submitted_proof = None

    def prove_turn_not_submitted(self, turn_id):
        value = self.not_submitted_proof
        if isinstance(value, dict):
            return dict(value)
        return value

    async def submit_prompt(self, **kwargs):
        self.submits.append(kwargs)
        return self.snapshot

    async def snapshot_conversation(self, conversation_url):
        self.reconciles.append(conversation_url)
        return self.snapshot

    async def recover_unpromoted_turn_snapshot(self, turn_id, *, expected_marker, timeout_seconds=None):
        self.unpromoted.append((turn_id, expected_marker, timeout_seconds))
        return self.snapshot


class V4BrowserEngineTests(unittest.TestCase):
    def intent(self):
        return {
            "intent_id": "intent-engine",
            "channel": "worker-1",
            "actor_id": "worker-1",
            "conversation_url": None,
            "payload_json": json.dumps({"prompt": "bounded task"}),
        }

    def test_parser_free_snapshot_is_submitted_but_not_captured(self):
        driver = FakeDriver(
            "Focused Window: Chrome\nhttps://chatgpt.com/c/engine-test\nassistant text"
        )
        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED", "channel": channel},
        )
        result = engine.submit(self.intent())
        self.assertEqual("SUBMITTED", result["status"])
        self.assertEqual("https://chatgpt.com/c/engine-test", result["conversation_url"])
        self.assertEqual("WORKER", driver.submits[0]["actor_kind"])
        self.assertEqual("intent-engine", driver.submits[0]["turn_id"])

    def test_parser_capture_and_reconcile_keep_url_identity(self):
        driver = FakeDriver("https://chatgpt.com/c/engine-test\nSCORP_RESULT")

        def parser(snapshot, intent_id):
            if "SCORP_RESULT" not in snapshot:
                return None
            return {"kind": "HANDOFF", "state": "DONE", "intent_id": intent_id}

        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED"},
            response_parser=parser,
        )
        captured = engine.submit(self.intent())
        self.assertEqual("RESPONSE_CAPTURED", captured["status"])
        self.assertEqual("https://chatgpt.com/c/engine-test", captured["conversation_url"])
        reconciled = engine.reconcile({
            **self.intent(),
            "conversation_url": "https://chatgpt.com/c/engine-test",
        })
        self.assertEqual("RESPONSE_CAPTURED", reconciled["status"])
        self.assertEqual(["https://chatgpt.com/c/engine-test"], driver.reconciles)

    def test_reconcile_recovers_unpromoted_worker_turn_without_resubmit(self):
        assignment_id = "assignment-unpromoted"
        driver = FakeDriver(
            "Focused Window: Chrome\nhttps://chatgpt.com/c/recovered\n"
            + assignment_id
            + "\nSCORP_RESULT"
        )

        def parser(snapshot, intent_id):
            if "SCORP_RESULT" not in snapshot:
                return None
            return {"kind": "HANDOFF", "assignment_id": assignment_id, "intent_id": intent_id}

        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED"},
            response_parser=parser,
            timeout_seconds=30,
        )
        intent = {
            **self.intent(),
            "intent_id": "worker-intent-" + assignment_id,
            "action_kind": "CHATGPT_WORKER_SUBMIT",
            "payload_json": json.dumps({
                "prompt": "bounded task",
                "worker_assignment": {"assignment_id": assignment_id},
            }),
        }
        result = engine.reconcile(intent)
        self.assertEqual("RESPONSE_CAPTURED", result["status"])
        self.assertEqual("https://chatgpt.com/c/recovered", result["conversation_url"])
        self.assertEqual(
            [("worker-intent-" + assignment_id, assignment_id, 30.0)],
            driver.unpromoted,
        )
        self.assertEqual([], driver.reconciles)

    def test_reconcile_missing_persisted_turn_binding_is_verified_not_submitted(self):
        intent_id = "master-reasoning-" + "9" * 32
        driver = FakeDriver("")
        driver.not_submitted_proof = {
            "proof": "PERSISTED_DRIVER_STATE_NO_TURN_BINDING_BEFORE_BROWSER_IO",
            "protocol_version": "scorp.chrome-use-driver/v1",
            "known_turn_count": 4,
        }
        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED"},
            response_parser=lambda snapshot, observed_intent_id: None,
            timeout_seconds=30,
        )
        result = engine.reconcile({
            "intent_id": intent_id,
            "channel": "master",
            "actor_id": "A",
            "action_kind": "MASTER_REASONING",
            "conversation_url": None,
            "payload_json": json.dumps({"prompt": "reason about durable state"}),
        })
        self.assertEqual("VERIFIED_NOT_SUBMITTED", result["status"])
        self.assertEqual(
            "PERSISTED_DRIVER_STATE_NO_TURN_BINDING_BEFORE_BROWSER_IO",
            result["proof"],
        )
        self.assertEqual([], driver.unpromoted)
        self.assertEqual([], driver.submits)

    def test_reconcile_recovers_unpromoted_master_reasoning_without_resubmit(self):
        intent_id = "master-reasoning-" + "a" * 32
        driver = FakeDriver(
            "Focused Window: Chrome\nhttps://chatgpt.com/c/master-recovered\n"
            + intent_id
            + "\nMASTER_DECISION"
        )

        def parser(snapshot, observed_intent_id):
            if "MASTER_DECISION" not in snapshot:
                return None
            return {
                "master_decision_version": 1,
                "intent_id": observed_intent_id,
                "action": "WAIT",
            }

        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED"},
            response_parser=parser,
            timeout_seconds=30,
        )
        intent = {
            "intent_id": intent_id,
            "channel": "master",
            "actor_id": "A",
            "action_kind": "MASTER_REASONING",
            "conversation_url": None,
            "payload_json": json.dumps({"prompt": "reason about durable state"}),
        }
        result = engine.reconcile(intent)
        self.assertEqual("RESPONSE_CAPTURED", result["status"])
        self.assertEqual("https://chatgpt.com/c/master-recovered", result["conversation_url"])
        self.assertEqual([(intent_id, intent_id, 30.0)], driver.unpromoted)
        self.assertEqual([], driver.submits)

    def test_reconcile_uses_durable_master_recovery_marker_from_payload(self):
        """A durable short marker must locate a crashed Master turn.

        The full Master prompt is intentionally large and may be truncated in
        an accessibility snapshot.  Recovery therefore cannot rely on the
        long intent id being visible in the browser.  The marker is persisted
        with the intent and is the only browser-side locator that is safe to
        use after a crash.
        """
        intent_id = "master-reasoning-" + "b" * 32
        recovery_marker = "SCORP_REASONING::" + "c" * 24
        driver = FakeDriver(
            "Focused Window: Chrome\nhttps://chatgpt.com/c/master-recovered-marker\n"
            + recovery_marker
            + "\nMASTER_DECISION"
        )

        def parser(snapshot, observed_intent_id):
            if recovery_marker not in snapshot or "MASTER_DECISION" not in snapshot:
                return None
            return {
                "master_decision_version": 1,
                "intent_id": observed_intent_id,
                "action": "WAIT",
            }

        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED"},
            response_parser=parser,
            timeout_seconds=30,
        )
        intent = {
            "intent_id": intent_id,
            "channel": "master",
            "actor_id": "A",
            "action_kind": "MASTER_REASONING",
            "conversation_url": None,
            "payload_json": json.dumps({
                "prompt": "{" + ("durable-state," * 500) + "}",
                "recovery_marker": recovery_marker,
            }),
        }
        result = engine.reconcile(intent)
        self.assertEqual("RESPONSE_CAPTURED", result["status"])
        self.assertEqual(
            [(intent_id, recovery_marker, 30.0)],
            driver.unpromoted,
        )
        self.assertEqual([], driver.submits)

    def test_reconcile_mismatched_conversation_is_ambiguous(self):
        driver = FakeDriver("https://chatgpt.com/c/other-conversation\nSCORP_RESULT")
        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED"},
            response_parser=lambda snapshot, intent_id: {"kind": "HANDOFF"},
        )
        result = engine.reconcile({
            **self.intent(),
            "conversation_url": "https://chatgpt.com/c/engine-test",
        })
        self.assertEqual("AMBIGUOUS", result["status"])
        self.assertEqual("CONVERSATION_URL_MISMATCH", result["reason"])


if __name__ == "__main__":
    unittest.main()
