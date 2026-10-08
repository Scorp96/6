from __future__ import annotations

import dataclasses
import unittest

from master_a_dynamic_v4.continuation_gate import ContinuationRequest, plan_continuation
from master_a_dynamic_v4.session_admission import AdmissionPolicy, SessionObservation

URL = "https://chatgpt.com/c/scorp-master-1"


def req(**kwargs):
    return dataclasses.replace(ContinuationRequest(
        project_id="scorp-r2-test",
        decision_id="decision-" + "a" * 32,
        decision_action="WAKE_MASTER",
        state_version=4,
        master_epoch=5,
        daemon_epoch=6,
    ), **kwargs)


def obs(**kwargs):
    return dataclasses.replace(SessionObservation(
        session_id="host-conversation-1",
        conversation_url=URL,
        role="MASTER",
        generation=9,
        auth_status="AUTHENTICATED",
        auth_verification="HOST_VERIFIED", physical_verification="HOST_VERIFIED",
        physical_status="VERIFIED",
        response_status="IDLE_CONFIRMED",
        unresolved_intents=0,
        model_label=None,
        model_verification="UNVERIFIED",
    ), **kwargs)


def pol(**kwargs):
    return dataclasses.replace(AdmissionPolicy(
        project_status="ACTIVE",
        operator_status="ACTIVE",
        required_role="MASTER",
        expected_generation=9,
        expected_conversation_url=URL,
    ), **kwargs)


class ContinuationGateTests(unittest.TestCase):
    def test_master_wake_proposes_no_send_candidate(self):
        r = plan_continuation(req(), obs(), pol())
        self.assertEqual("READY_FOR_GATED_ADAPTER", r.status)
        self.assertTrue(r.idempotency_key.startswith("continue-"))
        self.assertFalse(r.browser_send_authorized)

    def test_dedupes_same_event_after_restart(self):
        one = plan_continuation(req(), obs(), pol())
        two = plan_continuation(req(), obs(), pol(), already_queued={one.idempotency_key})
        self.assertEqual("ALREADY_QUEUED", two.status)
        self.assertEqual(one.idempotency_key, two.idempotency_key)
        self.assertFalse(two.browser_send_authorized)

    def test_event_and_generation_change_produce_distinct_keys(self):
        original = plan_continuation(req(), obs(), pol())
        new_event = plan_continuation(req(state_version=5), obs(), pol())
        new_generation = plan_continuation(req(), obs(generation=10), pol(expected_generation=10))
        self.assertNotEqual(original.idempotency_key, new_event.idempotency_key)
        self.assertNotEqual(original.idempotency_key, new_generation.idempotency_key)

    def test_terminal_and_emergency_stop_never_wake(self):
        for action in ("TERMINAL", "EMERGENCY_STOP", "BLOCKED"):
            with self.subTest(action=action):
                r = plan_continuation(req(decision_action=action), obs(), pol())
                self.assertEqual("STOP", r.status)
                self.assertIsNone(r.idempotency_key)

    def test_ambiguous_response_and_wait_do_not_wake(self):
        for action in ("RECONCILE_AMBIGUOUS", "RECONCILE_SUBMITTED",
                       "VERIFY_MASTER", "HEARTBEAT_IDLE", "RECOVER_STALLED",
                       "FENCE_STALE_RESULTS"):
            with self.subTest(action=action):
                r = plan_continuation(req(decision_action=action), obs(), pol())
                self.assertEqual("OBSERVE_ONLY", r.status)
                self.assertFalse(r.browser_send_authorized)

    def test_only_known_arbiter_actions_are_accepted(self):
        r = plan_continuation(req(decision_action="INJECT_PROMPT"), obs(), pol())
        self.assertEqual("BLOCKED", r.status)

    def test_worker_actions_require_matching_worker_role(self):
        r = plan_continuation(
            req(decision_action="ASSIGN_WORKER"),
            obs(role="WORKER"),
            pol(required_role="WORKER", active_workers=1, max_workers=2),
        )
        self.assertEqual("READY_FOR_GATED_ADAPTER", r.status)
        bad = plan_continuation(req(decision_action="RESUME_WORKER"), obs(), pol())
        self.assertEqual("BLOCKED", bad.status)
        self.assertEqual("ARBITER_ROLE_MISMATCH", bad.reason)

    def test_worker_capacity_fence_applies(self):
        r = plan_continuation(
            req(decision_action="ASSIGN_WORKER"),
            obs(role="WORKER"),
            pol(required_role="WORKER", active_workers=2, max_workers=2),
        )
        self.assertEqual("BLOCKED", r.status)
        self.assertEqual("WORKER_CAPACITY_EXHAUSTED", r.reason)

    def test_still_generating_and_unknown_state_never_wake(self):
        for response_status in ("GENERATING", "UNKNOWN"):
            with self.subTest(response_status=response_status):
                r = plan_continuation(req(), obs(response_status=response_status), pol())
                self.assertEqual("BLOCKED", r.status)
                self.assertEqual("RESPONSE_NOT_IDLE_CONFIRMED", r.reason)

    def test_ambiguous_browser_intents_block_even_when_arbiter_wakes(self):
        r = plan_continuation(req(), obs(unresolved_intents=1), pol())
        self.assertEqual("BLOCKED", r.status)
        self.assertEqual("UNRESOLVED_BROWSER_INTENTS", r.reason)

    def test_unknown_model_can_pass_neutral_scheduling_but_not_send(self):
        r = plan_continuation(req(), obs(model_label="GPT-6"), pol())
        self.assertEqual("READY_FOR_GATED_ADAPTER", r.status)
        self.assertFalse(r.browser_send_authorized)

    def test_old_model_requirement_is_not_bypassed(self):
        r = plan_continuation(
            req(), obs(model_label="GPT-6"), pol(required_model="GPT-5.6 Sol")
        )
        self.assertEqual("BLOCKED", r.status)
        self.assertEqual("REQUIRED_MODEL_UNVERIFIED", r.reason)

    def test_invalid_arbiter_identity_fails_closed(self):
        r = plan_continuation(req(decision_id="unknown"), obs(), pol())
        self.assertEqual("BLOCKED", r.status)
        self.assertEqual("ARBITER_IDENTITY_INVALID", r.reason)

    def test_invalid_epoch_is_blocked(self):
        r = plan_continuation(req(daemon_epoch=-1), obs(), pol())
        self.assertEqual("BLOCKED", r.status)
        self.assertEqual("EPOCH_OR_STATE_VERSION_INVALID", r.reason)

    def test_input_type_error(self):
        with self.assertRaisesRegex(TypeError, "CONTINUATION_REQUEST_REQUIRED"):
            plan_continuation({}, obs(), pol())


if __name__ == "__main__":
    unittest.main()
