from __future__ import annotations

import dataclasses
import unittest

from master_a_dynamic_v4.session_admission import (
    AdmissionPolicy, SessionObservation, evaluate_session,
)


URL = "https://chatgpt.com/c/session-123"


def session(**changes):
    original = SessionObservation(
        session_id="physical-window-1",
        conversation_url=URL,
        role="WORKER",
        generation=7,
        auth_status="AUTHENTICATED",
        auth_verification="HOST_VERIFIED", physical_verification="HOST_VERIFIED",
        physical_status="VERIFIED",
        response_status="IDLE_CONFIRMED",
        unresolved_intents=0,
    )
    return dataclasses.replace(original, **changes)


def policy(**changes):
    original = AdmissionPolicy(
        project_status="ACTIVE",
        operator_status="RUNNING",
        required_role="WORKER",
        expected_generation=7,
        expected_conversation_url=URL,
        active_workers=1,
        max_workers=2,
    )
    return dataclasses.replace(original, **changes)


class SessionAdmissionTests(unittest.TestCase):
    def assert_blocked(self, expected, obs=None, settings=None):
        result = evaluate_session(obs or session(), settings or policy())
        self.assertEqual("BLOCKED", result.status)
        self.assertEqual(expected, result.reason)
        self.assertFalse(result.browser_send_authorized)

    def test_unknown_model_is_eligible_under_model_neutral_policy(self):
        result = evaluate_session(session(), policy())
        self.assertEqual("ELIGIBLE_FOR_SCHEDULING", result.status)
        self.assertIsNone(result.model_label)
        self.assertEqual("UNVERIFIED", result.model_verification)
        self.assertFalse(result.browser_send_authorized)

    def test_model_name_alone_never_confers_required_model_eligibility(self):
        self.assert_blocked(
            "REQUIRED_MODEL_UNVERIFIED",
            obs=session(model_label="GPT-5.6 Sol"),
            settings=policy(required_model="GPT-5.6 Sol"),
        )

    def test_legacy_model_policy_must_be_explicitly_host_verified(self):
        allowed = evaluate_session(
            session(model_label="GPT-5.6 Sol", model_verification="HOST_VERIFIED"),
            policy(required_model="GPT-5.6 Sol"),
        )
        self.assertEqual("ELIGIBLE_FOR_SCHEDULING", allowed.status)
        self.assertFalse(allowed.browser_send_authorized)
        self.assert_blocked(
            "REQUIRED_MODEL_MISMATCH",
            obs=session(model_label="GPT-6", model_verification="HOST_VERIFIED"),
            settings=policy(required_model="GPT-5.6 Sol"),
        )

    def test_model_neutral_policy_can_handle_different_names(self):
        for name in (None, "GPT-6", "GPT-5.6 Sol", "Other GPT"):
            with self.subTest(name=name):
                result = evaluate_session(session(model_label=name), policy())
                self.assertEqual("ELIGIBLE_FOR_SCHEDULING", result.status)

    def test_subscription_text_does_not_attest_login_or_physical_session(self):
        self.assert_blocked(
            "HOST_AUTH_PROOF_UNVERIFIED",
            obs=session(model_label="GPT-6", auth_verification="UNVERIFIED"),
        )
        self.assert_blocked(
            "HOST_PHYSICAL_PROOF_UNVERIFIED",
            obs=session(physical_verification="UNVERIFIED"),
        )

    def test_cannot_resume_non_active_project(self):
        self.assert_blocked("PROJECT_NOT_ACTIVE", settings=policy(project_status="COMPLETE"))

    def test_paused_operator_blocks(self):
        self.assert_blocked("OPERATOR_NOT_RUNNING", settings=policy(operator_status="PAUSED"))

    def test_role_mismatch_blocks(self):
        self.assert_blocked("ROLE_MISMATCH", obs=session(role="MASTER"))

    def test_unknown_role_blocks(self):
        self.assert_blocked("ROLE_UNKNOWN", obs=session(role="OBSERVER"))

    def test_expired_epoch_blocks(self):
        self.assert_blocked("GENERATION_MISMATCH", obs=session(generation=6))

    def test_unsafe_generation_type_blocks(self):
        self.assert_blocked("GENERATION_MISMATCH", obs=session(generation=True))

    def test_mismatched_conversation_blocks(self):
        self.assert_blocked("CONVERSATION_BINDING_UNVERIFIED", obs=session(conversation_url="https://chatgpt.com/c/other"))

    def test_invalid_conversation_url_blocks(self):
        for url in ("http://chatgpt.com/c/session-123",
                    "https://chatgpt.com.evil.invalid/c/session-123",
                    "https://chatgpt.com/c/session-123?untrusted=yes",
                    "https://chatgpt.com/c/session-123/extra",
                    "https://chatgpt.com/"):
            with self.subTest(url=url):
                self.assert_blocked("CONVERSATION_BINDING_UNVERIFIED", obs=session(conversation_url=url))

    def test_auth_does_not_follow_from_page_model_label(self):
        self.assert_blocked(
            "AUTHENTICATION_UNVERIFIED",
            obs=session(auth_status="AUTH_PROBE_UNCERTAIN", model_label="GPT-6"),
        )

    def test_physical_binding_must_be_verified(self):
        self.assert_blocked("PHYSICAL_SESSION_UNVERIFIED", obs=session(physical_status="UNVERIFIED"))

    def test_ambiguous_browser_intents_block(self):
        self.assert_blocked("UNRESOLVED_BROWSER_INTENTS", obs=session(unresolved_intents=1))

    def test_unresolved_count_must_be_integer(self):
        self.assert_blocked("UNRESOLVED_INTENT_COUNT_INVALID", obs=session(unresolved_intents=True))

    def test_model_sending_while_active_is_forbidden(self):
        self.assert_blocked("RESPONSE_NOT_IDLE_CONFIRMED", obs=session(response_status="GENERATING"))

    def test_unknown_completion_state_is_not_idle(self):
        self.assert_blocked("RESPONSE_NOT_IDLE_CONFIRMED", obs=session(response_status="UNKNOWN"))

    def test_no_free_worker_capacity(self):
        self.assert_blocked("WORKER_CAPACITY_EXHAUSTED", settings=policy(active_workers=2))

    def test_capacity_is_bounded(self):
        self.assert_blocked("CAPACITY_POLICY_INVALID", settings=policy(max_workers=0))

    def test_unknown_n_model_sessions_do_not_expand_v4_parallel_capacity(self):
        self.assert_blocked("CAPACITY_POLICY_INVALID", settings=policy(max_workers=8))

    def test_master_identity_is_logical_and_not_model_label(self):
        result = evaluate_session(
            session(role="MASTER", model_label="GPT-6"),
            policy(required_role="MASTER", active_workers=2),
        )
        self.assertEqual("ELIGIBLE_FOR_SCHEDULING", result.status)
        self.assertFalse(result.browser_send_authorized)

    def test_invalid_inputs_do_not_get_dispatched(self):
        with self.assertRaisesRegex(TypeError, "SESSION_OBSERVATION_REQUIRED"):
            evaluate_session({}, policy())
        with self.assertRaisesRegex(TypeError, "SESSION_POLICY_REQUIRED"):
            evaluate_session(session(), {})


if __name__ == "__main__":
    unittest.main()
