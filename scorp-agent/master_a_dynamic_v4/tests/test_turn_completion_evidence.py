from __future__ import annotations

import dataclasses
import unittest

from master_a_dynamic_v4.turn_completion_evidence import TurnSample, assess_turn_completion

URL = "https://chatgpt.com/c/turn-finish-example"
HASH = "a" * 64


def sample(time_ms, **kw):
    s = TurnSample(
        session_id="master-physical-1",
        conversation_url=URL,
        binding_generation=4,
        intent_id="master-intent-2",
        sampled_at_ms=time_ms,
        generating=False,
        tool_pending=False,
        response_sha256=HASH,
        response_intent_verified=True,
        finish_event="TURN_FINAL_CONFIRMED",
        finish_event_provenance="HOST_VERIFIED",
    )
    return dataclasses.replace(s, **kw)


def decision(samples, **kwargs):
    args=dict(
        expected_session_id="master-physical-1",
        expected_conversation_url=URL,
        expected_binding_generation=4,
        expected_intent_id="master-intent-2",
    )
    args.update(kwargs)
    return assess_turn_completion(samples, **args)


class TurnCompletionEvidenceTests(unittest.TestCase):
    def assert_unknown(self, reason, a=None, b=None, **kw):
        result=decision([a or sample(1000), b or sample(5000)], **kw)
        self.assertEqual("UNKNOWN", result.status)
        self.assertEqual(reason, result.reason)
        self.assertFalse(result.browser_send_authorized)

    def test_two_host_terminal_proofs_confirm_idle_without_send(self):
        result=decision([sample(1000),sample(5000)])
        self.assertEqual("IDLE_CONFIRMED", result.status)
        self.assertEqual(HASH, result.proof)
        self.assertFalse(result.browser_send_authorized)

    def test_one_snapshot_never_proves_finished(self):
        result=decision([sample(1000)])
        self.assertEqual("TWO_FRESH_OBSERVATIONS_REQUIRED", result.reason)

    def test_two_observations_too_close_in_time_are_not_enough(self):
        self.assert_unknown("OBSERVATION_STABILITY_WINDOW_NOT_MET", b=sample(1999))

    def test_backwards_time_or_non_integer_time_is_rejected(self):
        self.assert_unknown("OBSERVATION_STABILITY_WINDOW_NOT_MET", b=sample(500))
        self.assert_unknown("HOST_SAMPLE_TIME_INVALID", b=sample(True))

    def test_one_very_old_sample_cannot_pair_with_fresh_terminal_observation(self):
        self.assert_unknown("OBSERVATION_PAIR_TOO_OLD", b=sample(180000))
        self.assert_unknown("EXPECTED_BINDING_INVALID", maximum_separation_ms=0)

    def test_last_two_must_both_be_host_verified(self):
        self.assert_unknown("HOST_TERMINAL_PROVENANCE_UNVERIFIED",
                            a=sample(1000, finish_event_provenance="MODEL_ASSERTED"))
        self.assert_unknown("HOST_TERMINAL_PROVENANCE_UNVERIFIED",
                            b=sample(5000, finish_event_provenance=None))

    def test_missing_finish_event_is_not_inferred_from_idle_screen(self):
        self.assert_unknown("HOST_TERMINAL_EVENT_MISSING",
                            b=sample(5000,finish_event=None))
        self.assert_unknown("HOST_TERMINAL_EVENT_MISSING",
                            b=sample(5000,finish_event="SEND_CONTROL_VISIBLE"))

    def test_progress_in_flight_not_assumed_done(self):
        result=decision([sample(1000),sample(5000,generating=True)])
        self.assertEqual("GENERATING",result.status)
        self.assertFalse(result.browser_send_authorized)
        self.assert_unknown("HOST_PROGRESS_STATE_UNVERIFIED",b=sample(5000,generating=None))
        self.assert_unknown("HOST_PROGRESS_STATE_UNVERIFIED",b=sample(5000,tool_pending=True))

    def test_wrong_conversation_and_session_are_not_accepted(self):
        self.assert_unknown("PHYSICAL_CONVERSATION_MISMATCH",
                            b=sample(5000,conversation_url="https://chatgpt.com/c/other"))
        self.assert_unknown("PHYSICAL_SESSION_MISMATCH",
                            b=sample(5000,session_id="different-session"))

    def test_epoch_and_turn_must_match(self):
        self.assert_unknown("BINDING_GENERATION_MISMATCH",b=sample(5000,binding_generation=5))
        self.assert_unknown("TURN_INTENT_MISMATCH",b=sample(5000,intent_id="other-intent"))

    def test_terminal_content_does_not_prove_intent(self):
        self.assert_unknown("RESPONSE_INTENT_NOT_VERIFIED",
                            b=sample(5000,response_intent_verified=False))

    def test_response_sha256_is_present_and_stable(self):
        self.assert_unknown("RESPONSE_CHANGED_BETWEEN_OBSERVATIONS",
                            b=sample(5000,response_sha256="b"*64))
        self.assert_unknown("STRUCTURED_RESPONSE_DIGEST_UNVERIFIED",
                            b=sample(5000,response_sha256=None))

    def test_invalid_expected_binding_fails_closed(self):
        self.assert_unknown("EXPECTED_BINDING_INVALID",expected_binding_generation=-1)
        self.assert_unknown("EXPECTED_BINDING_INVALID",minimum_separation_ms=0)
        self.assert_unknown("EXPECTED_CONVERSATION_URL_INVALID",
                            expected_conversation_url="https://evil.invalid/c/turn-finish-example")

    def test_restart_with_identical_snapshot_not_a_new_turn(self):
        result=decision([sample(1000), sample(1000)])
        self.assertEqual("UNKNOWN",result.status)
        self.assertEqual("OBSERVATION_STABILITY_WINDOW_NOT_MET",result.reason)


if __name__ == "__main__":
    unittest.main()
