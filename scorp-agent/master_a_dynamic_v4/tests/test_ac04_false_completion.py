from __future__ import annotations

import pathlib
import tempfile
import unittest


CANDIDATE = "a" * 40
ARTIFACT = "b" * 64


class FalseCompletionTests(unittest.TestCase):
    def make_store(self, root: pathlib.Path, *, required=("AC04",)):
        from master_a_dynamic_v4.state_store import StateStore

        store = StateStore(root / "state.sqlite3", allowed_roots=[root])
        contract = store.create_contract(
            "project-ac04",
            root_contract={"objective": "reject false completion"},
            acceptance_contract={"required": list(required)},
        )
        store.record_release_candidate("project-ac04", candidate_commit=CANDIDATE, manifest={"files": {"artifact": ARTIFACT}})
        return store, contract

    def record_valid_evidence(self, store, contract, *, candidate=CANDIDATE, observed=None):
        store.record_evidence_receipt(
            "project-ac04",
            "evidence-ac04",
            acceptance_id="AC04",
            result="PASS",
            candidate_commit=candidate,
            contract_sha256=contract["contract_sha256"],
            artifact_sha256=ARTIFACT,
            command_or_action="python -B -m unittest test_ac04_false_completion -v",
            observed_state=observed or {"exit_code": 0, "tests_run": 1, "failures": 0},
            raw_output_reference="C:/evidence/ac04.txt",
            started_at="2026-09-13T10:00:00Z",
            finished_at="2026-09-13T10:00:01Z",
        )

    def evaluate(self, store):
        from master_a_dynamic_v4.acceptance import AcceptanceValidator

        return AcceptanceValidator(store).evaluate("project-ac04", CANDIDATE, {"result": ARTIFACT})

    def test_valid_machine_evidence_can_pass_scoped_acceptance(self):
        with tempfile.TemporaryDirectory() as td:
            store, contract = self.make_store(pathlib.Path(td))
            try:
                self.record_valid_evidence(store, contract)
                decision = self.evaluate(store)
                self.assertEqual("PASS", decision.status.value)
                self.assertEqual((), decision.blockers)
            finally:
                store.close()

    def test_acceptance_finalize_atomically_completes_project_and_ends_master(self):
        from master_a_dynamic_v4.acceptance import AcceptanceValidator

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, contract = self.make_store(root)
            try:
                self.record_valid_evidence(store, contract)
                master = store.start_master_session("project-ac04", "master-a", ttl_seconds=300)
                decision = AcceptanceValidator(store).finalize("project-ac04", CANDIDATE, {})
                self.assertEqual("PASS", decision.status.value)
                state = store.get_project_state("project-ac04")
                self.assertEqual("COMPLETE", state["status"])
                self.assertEqual("COMPLETE", state["phase"])
                with store._connection() as conn:
                    session = conn.execute(
                        "select state,end_reason,lease_until from master_sessions where project_id=? and session_id=?",
                        ("project-ac04", "master-a"),
                    ).fetchone()
                    events = [r[0] for r in conn.execute(
                        "select kind from events where project_id=? order by kind",
                        ("project-ac04",),
                    ).fetchall()]
                self.assertEqual("ENDED", session["state"])
                self.assertEqual("PROJECT_COMPLETE", session["end_reason"])
                self.assertIn("PROJECT_COMPLETED", events)
                snapshot = store.activation_snapshot("project-ac04", daemon_epoch=1)
                self.assertEqual("COMPLETE", snapshot.project_status)
                second = AcceptanceValidator(store).finalize("project-ac04", CANDIDATE, {})
                self.assertEqual("PASS", second.status.value)
            finally:
                store.close()

    def test_acceptance_finalize_is_fail_closed_when_acceptance_is_blocked(self):
        from master_a_dynamic_v4.acceptance import AcceptanceValidator

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, _contract = self.make_store(root)
            try:
                store.start_master_session("project-ac04", "master-a", ttl_seconds=300)
                decision = AcceptanceValidator(store).finalize("project-ac04", CANDIDATE, {})
                self.assertEqual("BLOCKED", decision.status.value)
                self.assertEqual("ACTIVE", store.get_project_state("project-ac04")["status"])
                with store._connection() as conn:
                    state = conn.execute(
                        "select state from master_sessions where project_id=? and session_id=?",
                        ("project-ac04", "master-a"),
                    ).fetchone()[0]
                self.assertEqual("ACTIVE", state)
            finally:
                store.close()

    def test_missing_empty_fake_or_stale_evidence_is_rejected(self):
        variants = ("missing", "fake", "stale")
        for variant in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as td:
                store, contract = self.make_store(pathlib.Path(td))
                try:
                    if variant == "fake":
                        self.record_valid_evidence(store, contract, observed={"status": "PASS"})
                    elif variant == "stale":
                        self.record_valid_evidence(store, contract, candidate="c" * 40)
                    decision = self.evaluate(store)
                    self.assertNotEqual("PASS", decision.status.value)
                    joined = "|".join(decision.blockers)
                    self.assertIn(
                        {"missing": "MISSING_EVIDENCE", "fake": "SELF_ASSERTED_PASS", "stale": "EVIDENCE_CANDIDATE_MISMATCH"}[variant],
                        joined,
                    )
                finally:
                    store.close()

    def test_active_worker_unverified_task_and_unresolved_browser_intent_block_completion(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, contract = self.make_store(root)
            try:
                self.record_valid_evidence(store, contract)
                scheduler = Scheduler(store, "project-ac04", PathPolicy([root]), max_workers=2)
                scheduler.enqueue_graph([
                    {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [root / "result.txt"], "dependencies": []}
                ])
                scheduler.claim_runnable(master_epoch=0)
                store.prepare_intent("project-ac04", "intent-ac04", actor_id="A", channel="master", action_kind="CHATGPT_SUBMIT", payload={"prompt_sha256": "4" * 64})
                store.begin_possible_submit("intent-ac04")
                decision = self.evaluate(store)
                blockers = "|".join(decision.blockers)
                self.assertIn("REQUIRED_TASK_NOT_VERIFIED:T1", blockers)
                self.assertIn("ACTIVE_WORKERS", blockers)
                self.assertIn("BROWSER_INTENTS_UNRESOLVED", blockers)
            finally:
                store.close()


    def test_caller_supplied_artifact_set_cannot_override_release_manifest(self):
        from master_a_dynamic_v4.acceptance import AcceptanceValidator

        rogue = "c" * 64
        with tempfile.TemporaryDirectory() as td:
            store, contract = self.make_store(pathlib.Path(td))
            try:
                store.record_evidence_receipt(
                    "project-ac04",
                    "evidence-ac04",
                    acceptance_id="AC04",
                    result="PASS",
                    candidate_commit=CANDIDATE,
                    contract_sha256=contract["contract_sha256"],
                    artifact_sha256=rogue,
                    command_or_action="python -B -m unittest test_ac04_false_completion -v",
                    observed_state={"exit_code": 0, "tests_run": 1, "failures": 0},
                    raw_output_reference="C:/evidence/rogue.txt",
                    started_at="2026-09-14T13:00:00Z",
                    finished_at="2026-09-14T13:00:01Z",
                )
                decision = AcceptanceValidator(store).evaluate(
                    "project-ac04", CANDIDATE, {"caller_controlled": rogue}
                )
                self.assertEqual("BLOCKED", decision.status.value)
                self.assertTrue(
                    any("ARTIFACT" in blocker and "MISMATCH" in blocker for blocker in decision.blockers),
                    decision.blockers,
                )
            finally:
                store.close()

    def test_legacy_worker_result_cannot_satisfy_new_completion_gate(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, contract = self.make_store(root)
            try:
                self.record_valid_evidence(store, contract)
                scheduler = Scheduler(store, "project-ac04", PathPolicy([root]), max_workers=2)
                scheduler.enqueue_graph(
                    [{"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [root / "result.txt"]}]
                )
                claim = scheduler.claim_runnable(master_epoch=0)[0]
                result_id = scheduler.record_candidate(
                    claim.assignment_id,
                    lease_token=claim.lease_token,
                    master_epoch=claim.master_epoch,
                    kind="HANDOFF",
                    payload={"result_sha256": "a" * 64, "artifact_sha256": "a" * 64},
                )
                scheduler.verify_candidate(result_id, result_sha256="a" * 64)
                decision = self.evaluate(store)
                joined = "|".join(decision.blockers)
                self.assertIn("REQUIRED_TASK_STRUCTURED_RESULT_MISSING:T1", joined)
                self.assertIn("UNVERIFIED_CANDIDATE_RESULTS", joined)
            finally:
                store.close()


    def test_release_manifest_hash_mismatch_blocks_even_when_caller_and_evidence_match(self):
        from master_a_dynamic_v4.acceptance import AcceptanceValidator
        from master_a_dynamic_v4.models import canonical_json

        rogue = "c" * 64
        with tempfile.TemporaryDirectory() as td:
            store, contract = self.make_store(pathlib.Path(td))
            try:
                store.record_evidence_receipt(
                    "project-ac04",
                    "evidence-ac04",
                    acceptance_id="AC04",
                    result="PASS",
                    candidate_commit=CANDIDATE,
                    contract_sha256=contract["contract_sha256"],
                    artifact_sha256=rogue,
                    command_or_action="python -B -m unittest test_ac04_false_completion -v",
                    observed_state={"exit_code": 0, "tests_run": 1, "failures": 0},
                    raw_output_reference="C:/evidence/rogue-manifest.txt",
                    started_at="2026-09-14T13:00:00Z",
                    finished_at="2026-09-14T13:00:01Z",
                )
                with store._transaction() as conn:
                    conn.execute(
                        "UPDATE release_candidates SET manifest_json=? WHERE project_id=?",
                        (canonical_json({"files": {"artifact": rogue}}), "project-ac04"),
                    )
                decision = AcceptanceValidator(store).evaluate(
                    "project-ac04", CANDIDATE, {"caller_controlled": rogue}
                )
                self.assertEqual("BLOCKED", decision.status.value)
                self.assertIn("RELEASE_MANIFEST_HASH_MISMATCH", decision.blockers)
            finally:
                store.close()

    def test_unresolved_counterevidence_cannot_be_hidden_by_a_later_named_pass(self):
        with tempfile.TemporaryDirectory() as td:
            store, contract = self.make_store(pathlib.Path(td))
            try:
                # The lexical order is intentional: an invalid/blocked receipt
                # sorts before the PASS receipt.  Both must remain effective.
                store.record_evidence_receipt(
                    "project-ac04",
                    "a-blocked",
                    acceptance_id="AC04",
                    result="BLOCKED",
                    candidate_commit=CANDIDATE,
                    contract_sha256=contract["contract_sha256"],
                    artifact_sha256=ARTIFACT,
                    command_or_action="browser reconcile",
                    observed_state={"status": "BLOCKED_AMBIGUOUS"},
                    raw_output_reference="C:/evidence/blocked.txt",
                    started_at="2026-09-14T13:00:00Z",
                    finished_at="2026-09-14T13:00:01Z",
                )
                store.record_evidence_receipt(
                    "project-ac04",
                    "z-pass",
                    acceptance_id="AC04",
                    result="PASS",
                    candidate_commit=CANDIDATE,
                    contract_sha256=contract["contract_sha256"],
                    artifact_sha256=ARTIFACT,
                    command_or_action="python -B -m unittest test_ac04_false_completion -v",
                    observed_state={"exit_code": 0, "tests_run": 1, "failures": 0},
                    raw_output_reference="C:/evidence/pass.txt",
                    started_at="2026-09-14T13:00:00Z",
                    finished_at="2026-09-14T13:00:01Z",
                )
                decision = self.evaluate(store)
                self.assertEqual("BLOCKED", decision.status.value)
                self.assertIn("EVIDENCE_NOT_PASS:AC04:BLOCKED", decision.blockers)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
