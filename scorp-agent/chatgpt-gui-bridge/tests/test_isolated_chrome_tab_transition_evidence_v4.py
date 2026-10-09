from __future__ import annotations

import unittest

from isolated_chrome_tab_transition_evidence_v4 import (
    compare_created_blank_to_homepage,
)


def tab(i, *, url="about:blank", ownership="created",
        target=None, label=None, attached=True):
    return {
        "targetId": target or "target-" + str(i),
        "tabId": label or "tab-" + str(i),
        "url": url,
        "ownership": ownership,
        "type": "page",
        "relayAttached": attached,
    }


def frame(*tabs):
    return {"success": True, "data": {"full": True, "tabs": list(tabs)}}


OLD = frame(tab(1), tab(2))
ROOT = "https://chatgpt.com/"


class ChromeTabTransitionEvidenceTests(unittest.TestCase):
    def assert_no_authority(self, evidence):
        self.assertFalse(evidence.browser_send_authorized)
        self.assertFalse(evidence.host_completion_attested)
        self.assertFalse(evidence.authentication_attested)
        self.assertFalse(evidence.physical_tab_closed_proven)

    def test_reproduces_actual_20261009_chrome_home_replacement(self):
        result = compare_created_blank_to_homepage(
            OLD, frame(tab(3, url=ROOT)),
            expected_new_tab_id="tab-3",
        )
        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("PREVIOUS_TABS_NO_LONGER_LISTED", result.reason)
        self.assertEqual(2, result.old_count)
        self.assertEqual(1, result.after_count)
        self.assertEqual(0, result.old_exact_retained)
        self.assertEqual(2, result.old_no_longer_listed)
        self.assertEqual(1, result.previously_unknown_targets)
        self.assertEqual(1, result.new_created_home_count)
        self.assert_no_authority(result)

    def test_all_previous_tabs_retained_and_one_home_is_unattested_only(self):
        result = compare_created_blank_to_homepage(
            OLD, frame(tab(1), tab(2), tab(3, url=ROOT)),
            expected_new_tab_id="tab-3",
        )
        self.assertEqual("HOME_INVENTORY_CANDIDATE_UNATTESTED", result.status)
        self.assertEqual(2, result.old_exact_retained)
        self.assertEqual(0, result.old_no_longer_listed)
        self.assertEqual(1, result.previously_unknown_targets)
        self.assertEqual(1, result.new_created_home_count)
        self.assert_no_authority(result)

    def test_physical_closure_is_never_inferred_from_disappearance(self):
        result = compare_created_blank_to_homepage(
            OLD, frame(),
        )
        self.assertEqual("PREVIOUS_TABS_NO_LONGER_LISTED", result.reason)
        self.assertEqual(2, result.old_no_longer_listed)
        self.assertFalse(result.physical_tab_closed_proven)

    def test_existing_target_session_alias_changed_is_not_a_pass(self):
        result = compare_created_blank_to_homepage(
            OLD,
            frame(tab(1, label="renumbered"), tab(2), tab(3, url=ROOT)),
        )
        self.assertEqual("STABLE_TARGET_SESSION_LOCAL_ALIAS_CHANGED", result.reason)
        self.assertTrue(result.old_tab_alias_changed)
        self.assert_no_authority(result)

    def test_existing_tab_alias_reused_for_other_target_is_blocked(self):
        result = compare_created_blank_to_homepage(
            OLD,
            frame(tab(1, target="new-rebound-target"), tab(2), tab(3, url=ROOT)),
        )
        self.assertEqual("SESSION_TAB_ID_REUSED_FOR_OTHER_TARGET", result.reason)
        self.assertTrue(result.old_target_changed)
        self.assert_no_authority(result)

    def test_navigated_old_target_disallowed_even_when_id_stable(self):
        result = compare_created_blank_to_homepage(
            OLD,
            frame(tab(1, url=ROOT), tab(2), tab(3, url=ROOT)),
        )
        self.assertEqual("PRIOR_TAB_URL_OR_OWNERSHIP_CHANGED", result.reason)
        self.assert_no_authority(result)

    def test_unowned_new_tab_does_not_confirm_home(self):
        result = compare_created_blank_to_homepage(
            OLD,
            frame(tab(1),tab(2),tab(3,url=ROOT,ownership="foreign")),
        )
        self.assertEqual("EXPECTED_HOME_TAB_NOT_OBSERVED", result.reason)
        self.assert_no_authority(result)

    def test_unattached_new_tab_does_not_confirm_home(self):
        result = compare_created_blank_to_homepage(
            OLD, frame(tab(1),tab(2),tab(3,url=ROOT,attached=False)),
        )
        self.assertEqual("EXPECTED_HOME_TAB_NOT_OBSERVED", result.reason)

    def test_multiple_root_tabs_disqualify_exact_new_action(self):
        result = compare_created_blank_to_homepage(
            OLD, frame(tab(1),tab(2),tab(3,url=ROOT),tab(4,url=ROOT)),
        )
        self.assertEqual("EXACTLY_ONE_NEW_HOME_TAB_UNVERIFIED",result.reason)
        self.assert_no_authority(result)

    def test_new_tab_id_does_not_match_source_response(self):
        result = compare_created_blank_to_homepage(
            OLD, frame(tab(1),tab(2),tab(3,url=ROOT)),
            expected_new_tab_id="tab-unknown",
        )
        self.assertEqual("EXPECTED_NEW_TAB_IDENTITY_MISMATCH",result.reason)
        self.assert_no_authority(result)

    def test_duplicate_tab_id_blocks_before_any_comparison(self):
        bad = frame(tab(1),tab(2,label="tab-1"))
        result = compare_created_blank_to_homepage(OLD,bad)
        self.assertEqual("FULL_UNIQUE_TAB_INVENTORY_REQUIRED",result.reason)
        self.assert_no_authority(result)

    def test_duplicate_target_id_blocks_before_any_comparison(self):
        bad = frame(tab(1),tab(2,target="target-1"))
        self.assertEqual("FULL_UNIQUE_TAB_INVENTORY_REQUIRED",
                         compare_created_blank_to_homepage(OLD,bad).reason)

    def test_malformed_tab_list_or_status_is_not_a_tab_close(self):
        cases = (None, {}, {"success":False}, {"success":True},
                 {"success":True,"data":{"full":False,"tabs":[]}},
                 frame({"tabId":None,"targetId":"target-1"}),
                 {"success":True,"data":{"full":True,"tabs":"not-list"}})
        for bad in cases:
            with self.subTest(bad=type(bad).__name__):
                result = compare_created_blank_to_homepage(OLD,bad)
                self.assertEqual("FULL_UNIQUE_TAB_INVENTORY_REQUIRED",result.reason)
                self.assert_no_authority(result)

    def test_old_adopted_or_foreign_tabs_are_not_authorized_boundary(self):
        for owner in ("adopted","foreign"):
            with self.subTest(owner=owner):
                previous = frame(tab(1,ownership=owner),tab(2))
                result = compare_created_blank_to_homepage(
                    previous,frame(tab(1),tab(2),tab(3,url=ROOT)),
                )
                self.assertEqual("PREVIOUS_OWNED_BLANK_BOUNDARY_UNVERIFIED",result.reason)
                self.assert_no_authority(result)

    def test_no_external_side_effect_and_private_identifiers_never_in_result(self):
        before = frame(tab(1,target="private-1"),tab(2,target="private-2"))
        after = frame(tab(3,url=ROOT,target="private-3"))
        result = compare_created_blank_to_homepage(before,after)
        s = repr(result)
        for private in ("private-1","private-2","private-3",
                        "tab-1","tab-2","tab-3",ROOT):
            self.assertNotIn(private,s)
        self.assert_no_authority(result)

    def test_noncanonical_chatgpt_root_cannot_be_silently_allowed(self):
        result = compare_created_blank_to_homepage(
            OLD,frame(tab(1),tab(2),tab(3,url="https://chatgpt.com/c/foo")),
        )
        self.assertEqual("EXPECTED_HOME_TAB_NOT_OBSERVED",result.reason)
        self.assert_no_authority(result)

    def test_replaced_target_with_same_session_local_id_is_denied(self):
        result = compare_created_blank_to_homepage(
            OLD,frame(tab(3,url=ROOT,label="tab-1")),
        )
        self.assertEqual("SESSION_TAB_ID_REUSED_FOR_OTHER_TARGET",result.reason)
        self.assert_no_authority(result)


if __name__ == "__main__":
    unittest.main()
