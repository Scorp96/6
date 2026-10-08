from __future__ import annotations

from dataclasses import replace
import unittest

from master_a_dynamic_v4.turn_completion_evidence import TurnSample, assess_turn_completion
from master_a_dynamic_v4.host_terminal_receipt import (
    HostTerminalReceipt, verify_host_terminal_receipts,
)

import dataclasses
import hashlib
import hmac
import json


def seal_test_host_receipt(receipt, key):
    # TEST FIXTURE ONLY: no runtime signer is exported.
    if not isinstance(key,bytes) or len(key)<32:
        raise ValueError("HOST_RECEIPT_KEY_INVALID")
    d=dataclasses.asdict(receipt)
    d.pop("signature")
    payload=json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")
    sig=hmac.new(key,payload,hashlib.sha256).hexdigest()
    return dataclasses.replace(receipt,signature=sig)


URL="https://chatgpt.com/c/host-attested-test"
KEY=b"isolated-test-host-terminal-secret-32-bytes-long-20261009"
ALT=b"other-test-host-terminal-secret-32-bytes-long-20261009"
RESPONSE="a"*64


def make_sample(ms: int) -> TurnSample:
    return TurnSample(
        session_id="physical-session-1",conversation_url=URL,binding_generation=4,
        intent_id="intent-a",sampled_at_ms=ms,generating=False,tool_pending=False,
        response_sha256=RESPONSE,response_intent_verified=True,
        finish_event="TURN_FINAL_CONFIRMED",finish_event_provenance="HOST_VERIFIED",
    )


def make_receipt(sample: TurnSample, seq: int, *, key=KEY):
    fields=dict(
        protocol="scorp.browser-terminal-receipt/1",
        event_id="2"*32,sequence=seq,
        session_id=sample.session_id,
        conversation_url=sample.conversation_url,
        binding_generation=sample.binding_generation,
        intent_id=sample.intent_id,
        sampled_at_ms=sample.sampled_at_ms,
        response_sha256=sample.response_sha256,
        generating=sample.generating,
        tool_pending=sample.tool_pending,
        terminal_event="TURN_FINAL_CONFIRMED",
    )
    return seal_test_host_receipt(HostTerminalReceipt(**fields),key)


def evaluate(samples, receipts, key=KEY):
    return assess_turn_completion(
        samples,expected_session_id="physical-session-1",
        expected_conversation_url=URL,expected_binding_generation=4,
        expected_intent_id="intent-a",host_receipts=receipts,
        host_attestation_key=key,
    )


class HostTerminalReceiptTests(unittest.TestCase):
    def setUp(self):
        self.samples=[make_sample(1000),make_sample(5000)]
        self.receipts=[make_receipt(self.samples[0],11),make_receipt(self.samples[1],12)]

    def test_good_host_receipts_do_not_authorize_send(self):
        result=evaluate(self.samples,self.receipts)
        self.assertEqual("IDLE_CONFIRMED",result.status)
        self.assertFalse(result.browser_send_authorized)

    def test_unsigned_host_verified_strings_no_longer_count_as_proof(self):
        result=evaluate(self.samples,None)
        self.assertEqual("UNKNOWN",result.status)
        self.assertEqual("TWO_SIGNED_HOST_RECEIPTS_REQUIRED",result.reason)
        self.assertFalse(result.browser_send_authorized)

    def test_missing_host_secret_blocks_even_with_signed_evidence(self):
        result=evaluate(self.samples,self.receipts,key=None)
        self.assertEqual("HOST_ATTESTATION_KEY_UNAVAILABLE",result.reason)

    def test_key_mismatch_blocks(self):
        self.assertEqual("HOST_RECEIPT_SIGNATURE_INVALID",evaluate(self.samples,self.receipts,key=ALT).reason)

    def test_mutating_signed_terminal_event_blocks(self):
        altered=[self.receipts[0],replace(self.receipts[1],terminal_event="TURN_FINAL_CONFIRMED_EXTRA")]
        self.assertEqual("HOST_RECEIPT_TERMINAL_NOT_VERIFIED",evaluate(self.samples,altered).reason)

    def test_mutating_signed_intent_blocks(self):
        altered=[self.receipts[0],replace(self.receipts[1],intent_id="new-intent")]
        self.assertEqual("HOST_RECEIPT_SAMPLE_MISMATCH",evaluate(self.samples,altered).reason)

    def test_mutating_signed_timestamp_blocks(self):
        altered=[self.receipts[0],replace(self.receipts[1],sampled_at_ms=5001)]
        self.assertEqual("HOST_RECEIPT_SAMPLE_MISMATCH",evaluate(self.samples,altered).reason)

    def test_mutating_terminal_event_id_blocks(self):
        altered=[self.receipts[0],replace(self.receipts[1],event_id="4"*32)]
        self.assertEqual("HOST_TERMINAL_EVENT_ID_INVALID",evaluate(self.samples,altered).reason)

    def test_signer_supplying_different_event_id_even_if_signed_blocks(self):
        different=seal_test_host_receipt(replace(self.receipts[1],event_id="4"*32,signature=""),KEY)
        self.assertEqual("HOST_TERMINAL_EVENT_ID_INVALID",evaluate(self.samples,[self.receipts[0],different]).reason)

    def test_replay_or_reversed_sequence_blocks(self):
        altered=[self.receipts[0],replace(self.receipts[1],sequence=11)]
        self.assertEqual("HOST_RECEIPT_SEQUENCE_REPLAY",evaluate(self.samples,altered).reason)

    def test_attempt_to_flip_generating_bool_blocks(self):
        bad=replace(self.receipts[1],generating=True)
        self.assertEqual("HOST_RECEIPT_TERMINAL_NOT_VERIFIED",evaluate(self.samples,[self.receipts[0],bad]).reason)

    def test_attempt_to_flip_tool_pending_blocks(self):
        bad=replace(self.receipts[1],tool_pending=True)
        self.assertEqual("HOST_RECEIPT_TERMINAL_NOT_VERIFIED",evaluate(self.samples,[self.receipts[0],bad]).reason)

    def test_wrong_domain_even_if_signed_is_not_canonical(self):
        bad=seal_test_host_receipt(replace(self.receipts[1],conversation_url="https://evil.invalid/c/host-attested-test",signature=""),KEY)
        self.assertEqual("HOST_RECEIPT_CONVERSATION_INVALID",evaluate(self.samples,[self.receipts[0],bad]).reason)

    def test_wrong_response_digest_even_if_signed_does_not_match_sample(self):
        bad=seal_test_host_receipt(replace(self.receipts[1],response_sha256="b"*64,signature=""),KEY)
        self.assertEqual("HOST_RECEIPT_SAMPLE_MISMATCH",evaluate(self.samples,[self.receipts[0],bad]).reason)

    def test_unsigned_or_malformed_receipt_rejected(self):
        self.assertEqual("HOST_RECEIPT_SIGNATURE_INVALID",evaluate(self.samples,[self.receipts[0],replace(self.receipts[1],signature="not-signature")]).reason)

    def test_non_monotonic_receipt_sequence_cannot_reuse_old_event(self):
        bad=seal_test_host_receipt(replace(self.receipts[1],sequence=10,signature=""),KEY)
        self.assertEqual("HOST_RECEIPT_SEQUENCE_REPLAY",evaluate(self.samples,[self.receipts[0],bad]).reason)

    def test_nonstring_event_id_fails_closed_without_exception(self):
        malformed=replace(self.receipts[1],event_id=None)
        self.assertEqual("HOST_TERMINAL_EVENT_ID_INVALID",evaluate(self.samples,[self.receipts[0],malformed]).reason)

    def test_nonstring_conversation_url_fails_closed_without_exception(self):
        malformed=replace(self.receipts[1],conversation_url={"url":"fake"})
        self.assertEqual("HOST_RECEIPT_CONVERSATION_INVALID",evaluate(self.samples,[self.receipts[0],malformed]).reason)

    def test_host_receipt_unknown_shape_rejected(self):
        self.assertEqual("HOST_RECEIPT_SHAPE_INVALID",evaluate(self.samples,[self.receipts[0],object()]).reason)

    def test_secret_key_validation_no_weak_hmac(self):
        with self.assertRaisesRegex(ValueError,"HOST_RECEIPT_KEY_INVALID"):
            seal_test_host_receipt(self.receipts[0],b"short")


if __name__=="__main__":
    unittest.main()
