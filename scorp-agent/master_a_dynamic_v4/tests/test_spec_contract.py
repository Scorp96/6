from __future__ import annotations

import json
import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "docs" / "handoffs" / "SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml"
EXPECTED_BASELINE = "e87a9f70c55622e5ca742284e00918e13552f8ac"


class SpecContractTests(unittest.TestCase):
    def load_models(self):
        from master_a_dynamic_v4.models import AcceptanceStatus, CommitResult, IntentState

        return AcceptanceStatus, CommitResult, IntentState

    def load_spec(self):
        return json.loads(SPEC_PATH.read_text(encoding="utf-8"))

    def test_commit_results_are_a_closed_set(self):
        _, CommitResult, _ = self.load_models()
        self.assertEqual(
            {
                "COMMITTED",
                "ALREADY_COMMITTED",
                "VERSION_CONFLICT",
                "FENCED",
                "REJECTED",
            },
            {value.value for value in CommitResult},
        )

    def test_browser_intent_states_are_a_closed_set(self):
        _, _, IntentState = self.load_models()
        self.assertEqual(
            {
                "PREPARED",
                "MAY_HAVE_SUBMITTED",
                "CONFIRMED_SUBMITTED",
                "RESPONSE_CAPTURED",
                "VERIFIED_NOT_SUBMITTED",
                "BLOCKED_AMBIGUOUS",
            },
            {value.value for value in IntentState},
        )

    def test_acceptance_statuses_do_not_treat_not_run_as_pass(self):
        AcceptanceStatus, _, _ = self.load_models()
        self.assertEqual(
            {"PASS", "FAIL", "BLOCKED", "NOT_RUN"},
            {value.value for value in AcceptanceStatus},
        )
        self.assertNotEqual(AcceptanceStatus.PASS, AcceptanceStatus.NOT_RUN)

    def test_yaml_declares_exact_repository_and_sqlite_contract(self):
        spec = self.load_spec()
        self.assertEqual("scorp-v4-master-a-dynamic-workers/1.0", spec["spec_version"])
        self.assertEqual("Scorp96/666", spec["repository"]["slug"])
        self.assertEqual("p0/chatgpt-transport-bakeoff", spec["repository"]["baseline_branch"])
        self.assertEqual(EXPECTED_BASELINE, spec["repository"]["baseline_commit"])
        self.assertEqual("feature/v4-master-a-dynamic-workers", spec["repository"]["candidate_branch"])
        self.assertEqual("DELETE", spec["sqlite"]["journal_mode"])
        self.assertEqual("FULL", spec["sqlite"]["synchronous"])
        self.assertTrue(spec["sqlite"]["foreign_keys"])
        self.assertEqual(5000, spec["sqlite"]["busy_timeout_ms"])
        self.assertEqual(2, spec["workers"]["default_concurrency"])

    def test_yaml_lists_every_acceptance_case_and_scopes_short_soak(self):
        spec = self.load_spec()
        self.assertEqual([f"AC{index:02d}" for index in range(1, 13)], list(spec["acceptance"]))
        self.assertEqual("SHORT_SOAK_PERFORMANCE", spec["acceptance"]["AC12"]["mode"])
        self.assertGreaterEqual(spec["acceptance"]["AC12"]["requested_duration_seconds"], 1)
        self.assertFalse(spec["acceptance"]["AC12"]["long_duration_stability_proven"])
        self.assertRegex(spec["candidate_commit"], re.compile(r"^[0-9a-f]{40}$"))
        validation = json.loads(
            (REPO_ROOT / "docs" / "handoffs" / "SCORP_V4_GIT6_VALIDATION.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(validation["validated_commit"], spec["candidate_commit"])

    def test_yaml_marks_legacy_paths_soft_deprecated_without_deletion(self):
        spec = self.load_spec()
        self.assertEqual("SOFT_DEPRECATE", spec["deprecation"]["policy"])
        self.assertFalse(spec["deprecation"]["delete_before_production_cutover"])
        self.assertIn("project_state_v3.py", spec["deprecation"]["legacy_components"])
        self.assertIn("durable_actor_transport_v3.py", spec["deprecation"]["legacy_components"])


if __name__ == "__main__":
    unittest.main()
