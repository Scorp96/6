"""Offline acceptance tests: no Chrome, no network, no GPT or Codex inference."""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from relay import GateError, Relay


class RelayAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.relay = Relay(Path(self.tmp.name) / "isolated.sqlite3")
        self.identity = {
            "request_id": "canary-turn-001",
            "project_id": "scorp-canary",
            "conversation_url": "https://chatgpt.com/c/test-canary-001",
            "master_epoch": 1,
            "turn_no": 1,
            "prompt_sha256": hashlib.sha256(b"GPT WORK REQUEST").hexdigest(),
        }

    def tearDown(self):
        self.relay.close()
        self.tmp.cleanup()

    def prepare(self):
        return self.relay.prepare(**self.identity)

    def test_prepare_idempotence(self):
        first = self.prepare()
        again = self.prepare()
        self.assertEqual(first, again)
        self.assertEqual(self.relay.verify_journal(first["request_id"])["event_count"], 1)

    def test_request_identity_conflict(self):
        self.prepare()
        with self.assertRaisesRegex(GateError, "REQUEST_IDENTITY_COLLISION"):
            self.relay.prepare(**{**self.identity, "master_epoch": 2})

    def test_same_epoch_turn_rejects_different_request(self):
        self.prepare()
        with self.assertRaises(sqlite3.IntegrityError):
            self.relay.prepare(**{**self.identity, "request_id": "canary-turn-999"})

    def test_claim_is_fenced(self):
        self.prepare()
        a = self.relay.claim(self.identity["request_id"], "codex-local")
        b = self.relay.claim(self.identity["request_id"], "codex-local")
        self.assertEqual(a, b)
        with self.assertRaisesRegex(GateError, "ALREADY_CLAIMED"):
            self.relay.claim(self.identity["request_id"], "other-recorder")
        with self.assertRaisesRegex(GateError, "CLAIM_BINDING_INVALID"):
            self.relay.arm(self.identity["request_id"], "other-recorder")

    def test_ambiguous_submit_never_retries(self):
        self.prepare()
        self.relay.claim(self.identity["request_id"], "codex-local")
        self.relay.arm(self.identity["request_id"], "codex-local")
        with self.assertRaisesRegex(GateError, "ACTIVATION_UNCERTAIN_NO_RETRY"):
            self.relay.claim(self.identity["request_id"], "codex-local")
        with self.assertRaisesRegex(GateError, "CLAIM_BINDING_INVALID"):
            self.relay.arm(self.identity["request_id"], "codex-local")
        state = self.relay.inspect(self.identity["request_id"])
        self.assertEqual(state["next_action"], "RECONCILE_READ_ONLY_NO_SEND")
        self.assertFalse(state["safe_to_retry_send"])

    def test_mismatched_conversation_rejected(self):
        self.prepare()
        self.relay.claim(self.identity["request_id"], "codex-local")
        self.relay.arm(self.identity["request_id"], "codex-local")
        with self.assertRaisesRegex(GateError, "CONVERSATION_BINDING_MISMATCH"):
            self.relay.record_observation(
                self.identity["request_id"], "codex-local",
                "https://chatgpt.com/c/unexpected", "a" * 64, "snapshot-001")
        self.assertEqual(self.relay.inspect(self.identity["request_id"])["state"],
                         "MAY_HAVE_SUBMITTED")

    def test_observation_only_not_live_verification(self):
        self.prepare()
        self.relay.claim(self.identity["request_id"], "codex-local")
        self.relay.arm(self.identity["request_id"], "codex-local")
        recorded = self.relay.record_observation(
            self.identity["request_id"], "codex-local",
            self.identity["conversation_url"], "a" * 64, "fixture-001")
        self.assertEqual(recorded["state"], "OBSERVED_UNVERIFIED")
        result = self.relay.verify_journal(self.identity["request_id"])
        self.assertTrue(result["chain_valid"])
        self.assertEqual(result["event_count"], 4)
        with self.assertRaisesRegex(GateError, "NOT_AWAITING_BROWSER_RESULT"):
            self.relay.record_observation(
                self.identity["request_id"], "codex-local",
                self.identity["conversation_url"], "a" * 64, "fixture-001")

    def test_expired_claim_safe_reacquire_only_pre_submit(self):
        self.prepare()
        self.relay.claim(self.identity["request_id"], "codex-local")
        self.relay.db.execute(
            "UPDATE activations SET lease_until=0 WHERE request_id=?",
            (self.identity["request_id"],))
        self.relay.claim(self.identity["request_id"], "codex-new-recorder")
        self.assertEqual(self.relay.inspect(self.identity["request_id"])["claimed_by"],
                         "codex-new-recorder")
        self.relay.arm(self.identity["request_id"], "codex-new-recorder")
        self.relay.db.execute(
            "UPDATE activations SET lease_until=0 WHERE request_id=?",
            (self.identity["request_id"],))
        with self.assertRaisesRegex(GateError, "ACTIVATION_UNCERTAIN_NO_RETRY"):
            self.relay.claim(self.identity["request_id"], "codex-third")

    def test_invalid_url_rejected(self):
        with self.assertRaisesRegex(GateError, "PINNED_CONVERSATION_URL_INVALID"):
            self.relay.prepare(**{**self.identity, "conversation_url": "https://evil.example/c/123"})

    def test_event_tamper_detected(self):
        self.prepare()
        self.relay.db.execute(
            "UPDATE journal SET event_json='{}' WHERE request_id=?",
            (self.identity["request_id"],))
        with self.assertRaisesRegex(GateError, "JOURNAL_HASH_CHAIN_BROKEN"):
            self.relay.verify_journal(self.identity["request_id"])

    def test_event_timestamp_tamper_detected(self):
        self.prepare()
        self.relay.db.execute(
            "UPDATE journal SET written_at=written_at+1 WHERE request_id=?",
            (self.identity["request_id"],))
        with self.assertRaisesRegex(GateError, "JOURNAL_HASH_CHAIN_BROKEN"):
            self.relay.verify_journal(self.identity["request_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
