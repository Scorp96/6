from __future__ import annotations

from dataclasses import replace
import unittest

from master_a_dynamic_v4.continuation_gate import ContinuationRequest
from master_a_dynamic_v4.session_admission import SessionObservation, AdmissionPolicy
from master_a_dynamic_v4.turn_completion_evidence import TurnSample
from master_a_dynamic_v4.verified_continuation import plan_with_verified_turn

URL="https://chatgpt.com/c/bound-worker"
SHA="1"*64

def request():
    return ContinuationRequest(
        project_id="r2-isolated",
        decision_id="decision-"+"c"*32,
        decision_action="RESUME_WORKER",
        state_version=3,master_epoch=4,daemon_epoch=5,
    )

def observation(**changes):
    row=SessionObservation(
        session_id="host-worker-01",conversation_url=URL,role="WORKER",
        generation=6,auth_status="AUTHENTICATED",
        auth_verification="HOST_VERIFIED", physical_verification="HOST_VERIFIED",physical_status="VERIFIED",
        response_status="IDLE_CONFIRMED",unresolved_intents=0,model_label="GPT-6",
    )
    return replace(row,**changes)

def policy(**changes):
    row=AdmissionPolicy(
        project_status="ACTIVE",operator_status="RUNNING",required_role="WORKER",
        expected_generation=6,expected_conversation_url=URL,
        active_workers=1,max_workers=2,
    )
    return replace(row,**changes)

def samples(**changes):
    row=TurnSample(
        session_id="host-worker-01",conversation_url=URL,
        binding_generation=6,intent_id="intent-current",
        sampled_at_ms=1000,generating=False,tool_pending=False,
        response_sha256=SHA,response_intent_verified=True,
        finish_event="TURN_FINAL_CONFIRMED",finish_event_provenance="HOST_VERIFIED",
    )
    return [row,replace(row,sampled_at_ms=5000,**changes)]

def plan(rows=None,**changes):
    args=dict(expected_intent_id="intent-current",now_monotonic_ms=6000)
    args.update(changes)
    return plan_with_verified_turn(request(),observation(),policy(),rows or samples(),**args)

class VerifiedContinuationTests(unittest.TestCase):
    def test_good_host_proof_allows_candidate_not_send(self):
        result=plan()
        self.assertEqual("READY_FOR_GATED_ADAPTER",result.status)
        self.assertEqual(SHA,result.completion_proof_sha256)
        self.assertFalse(result.browser_send_authorized)

    def test_emergency_stop_does_not_need_browser_observations(self):
        for action, expected in (
            ("TERMINAL", "STOP"),
            ("EMERGENCY_STOP", "STOP"),
            ("HEARTBEAT_IDLE", "OBSERVE_ONLY"),
            ("RECONCILE_AMBIGUOUS", "OBSERVE_ONLY"),
        ):
            with self.subTest(action=action):
                result=plan_with_verified_turn(
                    replace(request(), decision_action=action),
                    observation(), policy(), [], expected_intent_id="intent-current",
                    now_monotonic_ms=6000,
                )
                self.assertEqual(expected,result.status)
                self.assertFalse(result.browser_send_authorized)

    def test_unverified_idle_string_does_not_bypass_proof(self):
        r=plan(samples(finish_event=None))
        self.assertEqual("BLOCKED",r.status)
        self.assertIn("HOST_TERMINAL_EVENT_MISSING",r.reason)

    def test_stale_first_observation_does_not_reuse_terminal_proof(self):
        rows=samples()
        from dataclasses import replace as _replace
        rows=[_replace(rows[0],sampled_at_ms=1000),
              _replace(rows[1],sampled_at_ms=62000)]
        r=plan(rows,now_monotonic_ms=63000)
        self.assertEqual("BLOCKED",r.status)
        self.assertIn("OBSERVATION_PAIR_TOO_OLD",r.reason)

    def test_too_old_observation_fails_closed(self):
        r=plan(now_monotonic_ms=60000)
        self.assertEqual("BLOCKED",r.status)
        self.assertEqual("HOST_OBSERVATION_STALE",r.reason)

    def test_future_sample_or_invalid_monotonic_clock_blocks(self):
        self.assertEqual("HOST_OBSERVATION_IN_FUTURE",plan(now_monotonic_ms=4999).reason)
        self.assertEqual("HOST_CLOCK_INVALID",plan(now_monotonic_ms=-1).reason)

    def test_unverified_model_does_not_override_required_policy(self):
        r=plan_with_verified_turn(
            request(),observation(),policy(required_model="GPT-5.6 Sol"),
            samples(),expected_intent_id="intent-current",now_monotonic_ms=6000,
        )
        self.assertEqual("BLOCKED",r.status)
        self.assertEqual("REQUIRED_MODEL_UNVERIFIED",r.reason)

    def test_recovered_work_does_not_duplicate_candidate(self):
        original=plan()
        repeated=plan(already_queued={original.idempotency_key})
        self.assertEqual("ALREADY_QUEUED",repeated.status)
        self.assertEqual(original.idempotency_key,repeated.idempotency_key)

    def test_wrong_intent_blocks(self):
        r=plan(expected_intent_id="foreign-intent")
        self.assertEqual("BLOCKED",r.status)
        self.assertIn("TURN_INTENT_MISMATCH",r.reason)

    def test_gpt_generating_does_not_wake_even_if_host_event_present(self):
        r=plan(samples(generating=True))
        self.assertEqual("BLOCKED",r.status)
        self.assertIn("HOST_REPORTS_GENERATING",r.reason)

    def test_conversation_mismatch_blocks(self):
        r=plan(samples(conversation_url="https://chatgpt.com/c/other"))
        self.assertEqual("BLOCKED",r.status)
        self.assertIn("PHYSICAL_CONVERSATION_MISMATCH",r.reason)

    def test_hard_blocked_operator_prevents_continuation(self):
        r=plan_with_verified_turn(
            request(),observation(),policy(operator_status="PAUSED"),
            samples(),expected_intent_id="intent-current",now_monotonic_ms=6000
        )
        self.assertEqual("BLOCKED",r.status)
        self.assertEqual("OPERATOR_NOT_RUNNING",r.reason)


if __name__=="__main__":
    unittest.main()
