from __future__ import annotations

import dataclasses
import unittest

from master_a_dynamic_v4.session_admission import AdmissionPolicy, SessionObservation
from master_a_dynamic_v4.session_selection import HostBinding, shortlist_workers


def worker(i: int, **kw):
    o = SessionObservation(
        session_id=f"gpt-session-{i:03d}",
        conversation_url=f"https://chatgpt.com/c/gpt-{i:03d}",
        role="WORKER",
        generation=3,
        auth_status="AUTHENTICATED",
        auth_verification="HOST_VERIFIED", physical_verification="HOST_VERIFIED",
        physical_status="VERIFIED",
        response_status="IDLE_CONFIRMED",
        unresolved_intents=0,
        model_label="GPT-6" if i % 2 == 0 else "GPT-5.6 Sol",
        model_verification="UNVERIFIED",
    )
    return dataclasses.replace(o, **kw)


def bindings(*rows):
    return {s.session_id: HostBinding(s.conversation_url, 3) for s in rows}


def policy(**kw):
    p = AdmissionPolicy(
        project_status="ACTIVE",
        operator_status="ACTIVE",
        required_role="WORKER",
        expected_generation=0,  # per-host binding overrides this placeholder
        expected_conversation_url="https://chatgpt.com/c/not-assigned",
        active_workers=0,
        max_workers=2,
    )
    return dataclasses.replace(p, **kw)


class SessionSelectionTests(unittest.TestCase):
    def test_one_hundred_known_conversations_only_fill_two_slots(self):
        many = [worker(i) for i in range(100)]
        selected = shortlist_workers(many, bindings(*many), policy())
        self.assertEqual(("gpt-session-000", "gpt-session-001"), selected.selected_session_ids)
        self.assertEqual(98, len(selected.skipped))
        self.assertFalse(selected.browser_send_authorized)

    def test_order_is_independent_of_input_enumeration(self):
        many = [worker(i) for i in range(7)]
        first = shortlist_workers(many, bindings(*many), policy())
        last = shortlist_workers(list(reversed(many)), bindings(*many), policy())
        self.assertEqual(first, last)

    def test_one_active_worker_yields_only_one_slot(self):
        many = [worker(i) for i in range(5)]
        result = shortlist_workers(many, bindings(*many), policy(active_workers=1))
        self.assertEqual(("gpt-session-000",), result.selected_session_ids)

    def test_candidate_cannot_self_assert_different_binding_generation(self):
        o = worker(1, generation=5)
        selected = shortlist_workers([o], bindings(o), policy())
        self.assertEqual((), selected.selected_session_ids)
        self.assertIn((o.session_id, "GENERATION_MISMATCH"), selected.skipped)

    def test_binding_must_exist_in_host_mapping(self):
        o = worker(1)
        result = shortlist_workers([o], {}, policy())
        self.assertEqual((), result.selected_session_ids)
        self.assertIn((o.session_id, "HOST_BINDING_MISSING"), result.skipped)

    def test_stale_url_does_not_match_authoritative_binding(self):
        o = worker(1)
        binding = {o.session_id: HostBinding("https://chatgpt.com/c/other", 3)}
        result = shortlist_workers([o], binding, policy())
        self.assertIn((o.session_id, "CONVERSATION_BINDING_UNVERIFIED"), result.skipped)

    def test_duplicate_session_id_is_never_selected(self):
        o = worker(1)
        result = shortlist_workers([o, o], bindings(o), policy())
        self.assertEqual((), result.selected_session_ids)
        self.assertIn((o.session_id, "DUPLICATE_SESSION_ID"), result.skipped)

    def test_duplicate_conversation_is_not_selected_twice(self):
        o1 = worker(1)
        o2 = worker(2, conversation_url=o1.conversation_url)
        result = shortlist_workers([o1, o2], bindings(o1, o2), policy())
        self.assertEqual((o1.session_id,), result.selected_session_ids)
        self.assertIn((o2.session_id, "DUPLICATE_PHYSICAL_CONVERSATION"), result.skipped)

    def test_active_generation_and_login_guard_held_for_each_session(self):
        a = worker(1, auth_status="AUTH_PROBE_UNCERTAIN")
        b = worker(2)
        result = shortlist_workers([a, b], bindings(a, b), policy())
        self.assertEqual((b.session_id,), result.selected_session_ids)
        self.assertIn((a.session_id, "AUTHENTICATION_UNVERIFIED"), result.skipped)

    def test_legacy_required_model_remains_fenced(self):
        a = worker(1, model_label="GPT-5.6 Sol", model_verification="HOST_VERIFIED")
        b = worker(2, model_label="GPT-6", model_verification="HOST_VERIFIED")
        result = shortlist_workers([a, b], bindings(a, b), policy(required_model="GPT-5.6 Sol"))
        self.assertEqual((a.session_id,), result.selected_session_ids)
        self.assertIn((b.session_id, "REQUIRED_MODEL_MISMATCH"), result.skipped)

    def test_wrong_master_role_rejected(self):
        with self.assertRaisesRegex(ValueError, "SHORTLIST_WORKER_ROLE_REQUIRED"):
            shortlist_workers([], {}, policy(required_role="MASTER"))

    def test_unverified_v4_concurrency_expansion_rejected(self):
        with self.assertRaisesRegex(ValueError, "V4_WORKER_CAPACITY_INVALID"):
            shortlist_workers([], {}, policy(max_workers=8))


if __name__ == "__main__":
    unittest.main()
