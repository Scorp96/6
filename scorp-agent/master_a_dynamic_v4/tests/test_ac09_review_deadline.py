from __future__ import annotations

import pathlib
import tempfile
import unittest


class ReviewDeadlineTests(unittest.TestCase):
    def test_late_counter_evidence_is_append_only_keeps_historical_verdict_and_blocks_acceptance(self):
        from master_a_dynamic_v4.acceptance import AcceptanceValidator
        from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            contract = store.create_contract("project-ac09", root_contract={"objective": "late evidence"}, acceptance_contract={"required": ["AC09"]})
            store.record_release_candidate("project-ac09", candidate_commit="a" * 40, manifest={"files": {"artifact": "b" * 64}})
            store.record_evidence_receipt(
                "project-ac09", "evidence-ac09", acceptance_id="AC09", result="PASS", candidate_commit="a" * 40,
                contract_sha256=contract["contract_sha256"], artifact_sha256="b" * 64,
                command_or_action="review deadline acceptance", observed_state={"exit_code": 0, "finding_count": 0},
                raw_output_reference="C:/evidence/ac09.txt", started_at="2026-09-13T09:59:00Z", finished_at="2026-09-13T09:59:30Z",
            )
            try:
                store.open_review("project-ac09", "review-1", deadline="2026-09-13T10:00:00Z")
                first = store.record_review_finding("project-ac09", "review-1", "finding-before", severity="info", blocking=False, payload={"note": "observed"}, observed_at="2026-09-13T09:59:59Z")
                self.assertFalse(first["late"])
                store.close_review("project-ac09", "review-1", verdict="PASS", closed_at="2026-09-13T10:00:00Z")
                late = store.record_review_finding("project-ac09", "review-1", "finding-late", severity="critical", blocking=True, payload={"counter_evidence": "hash mismatch"}, observed_at="2026-09-13T10:01:00Z")
                self.assertTrue(late["late"])
                review = store.get_review("project-ac09", "review-1")
                self.assertEqual("PASS", review["verdict"])
                self.assertEqual(1, review["late_finding_count"])
                with self.assertRaises(StoreInvariantError):
                    store.record_review_finding("project-ac09", "review-1", "finding-late", severity="low", blocking=False, payload={"rewrite": True}, observed_at="2026-09-13T10:02:00Z")
                decision = AcceptanceValidator(store).evaluate("project-ac09", "a" * 40, {"artifact": "b" * 64})
                self.assertEqual("BLOCKED", decision.status.value)
                self.assertIn("BLOCKING_FINDINGS", decision.blockers)
                self.assertIn("LATE_BLOCKING_FINDINGS", decision.blockers)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
