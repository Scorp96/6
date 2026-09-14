from __future__ import annotations

import unittest
import pathlib
import tempfile


class WorkResultContractTests(unittest.TestCase):
    def valid_payload(self) -> dict:
        return {
            "work_result_version": "1",
            "project_id": "project-1",
            "worker_id": "worker-1",
            "assignment_id": "assignment-1",
            "task_id": "T1",
            "objective_sha256": "a" * 64,
            "base_state_version": 3,
            "candidate_commit": "b" * 40,
            "status": "COMPLETE",
            "scope_completed": ["implementation"],
            "scope_not_completed": [],
            "deliverables": [{"path": "report.md", "sha256": "c" * 64}],
            "evidence": [{"ref": "evidence-1", "sha256": "d" * 64}],
            "acceptance_coverage": ["AC01"],
            "facts": ["the tested behavior is present"],
            "inferences": [],
            "unknowns": [],
            "contradictions": [],
            "followup_proposals": [],
            "result_sha256": "e" * 64,
        }

    def validate(self, payload: dict):
        from master_a_dynamic_v4.work_result import validate_work_result

        return validate_work_result(
            payload,
            project_id="project-1",
            assignment_id="assignment-1",
            task_id="T1",
            objective_sha256="a" * 64,
            base_state_version=3,
        )

    def test_accepts_complete_result_with_identity_and_evidence(self):
        result = self.validate(self.valid_payload())
        self.assertEqual("COMPLETE", result["status"])
        self.assertEqual("T1", result["task_id"])

    def test_rejects_result_bound_to_wrong_assignment_or_version(self):
        from master_a_dynamic_v4.work_result import WorkResultRejected

        for field, value, code in (
            ("assignment_id", "assignment-2", "ASSIGNMENT_ID_MISMATCH"),
            ("project_id", "project-2", "PROJECT_ID_MISMATCH"),
            ("task_id", "T2", "TASK_ID_MISMATCH"),
            ("objective_sha256", "f" * 64, "OBJECTIVE_HASH_MISMATCH"),
            ("base_state_version", 4, "BASE_STATE_VERSION_MISMATCH"),
        ):
            with self.subTest(field=field):
                payload = self.valid_payload()
                payload[field] = value
                with self.assertRaisesRegex(WorkResultRejected, code):
                    self.validate(payload)

    def test_rejects_complete_result_without_machine_evidence(self):
        from master_a_dynamic_v4.work_result import WorkResultRejected

        payload = self.valid_payload()
        payload["evidence"] = []
        with self.assertRaisesRegex(WorkResultRejected, "COMPLETE_EVIDENCE_MISSING"):
            self.validate(payload)

    def test_rejects_unknown_status_and_malformed_required_fields(self):
        from master_a_dynamic_v4.work_result import WorkResultRejected

        payload = self.valid_payload()
        payload["status"] = "DONE"
        with self.assertRaisesRegex(WorkResultRejected, "STATUS_INVALID"):
            self.validate(payload)

        payload = self.valid_payload()
        payload["work_result_version"] = "2"
        with self.assertRaisesRegex(WorkResultRejected, "WORK_RESULT_VERSION_UNSUPPORTED"):
            self.validate(payload)

    def test_scheduler_admits_only_result_bound_to_claim_and_graph_version(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler, WorkerFenceError
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            store.create_contract(
                "project-1",
                root_contract={"objective": "structured result"},
                acceptance_contract={"required": ["AC01"]},
            )
            scheduler = Scheduler(store, "project-1", PathPolicy([root]), max_workers=2)
            try:
                scheduler.enqueue_graph(
                    [{"task_id": "T1", "objective_sha256": "a" * 64, "resource_scope": [root / "out.txt"]}]
                )
                claim = scheduler.claim_runnable(master_epoch=0)[0]
                payload = self.valid_payload()
                payload.update(
                    {
                        "worker_id": claim.worker_id,
                        "assignment_id": claim.assignment_id,
                        "base_state_version": claim.base_state_version,
                    }
                )
                result_id = scheduler.record_work_result(claim, payload=payload)
                scheduler.verify_candidate(result_id, result_sha256=payload["result_sha256"])
                self.assertEqual("ACCEPTED", scheduler.get_task("T1")["state"])

                claim2 = scheduler.claim_runnable(master_epoch=0)
                self.assertEqual([], claim2)
            finally:
                store.close()

    def test_scheduler_fences_work_result_from_newer_project_state(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler, WorkerFenceError
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            store.create_contract(
                "project-1",
                root_contract={"objective": "structured result"},
                acceptance_contract={"required": ["AC01"]},
            )
            scheduler = Scheduler(store, "project-1", PathPolicy([root]), max_workers=2)
            try:
                scheduler.enqueue_graph(
                    [{"task_id": "T1", "objective_sha256": "a" * 64, "resource_scope": [root / "out.txt"]}]
                )
                claim = scheduler.claim_runnable(master_epoch=0)[0]
                proposal = {"project_id": "project-1", "master_identity": "A", "kind": "SET_PHASE", "phase": "WORK"}
                self.assertEqual("COMMITTED", store.commit(0, 0, "advance-before-result", proposal, []).value)
                payload = self.valid_payload()
                payload.update(
                    {
                        "worker_id": claim.worker_id,
                        "assignment_id": claim.assignment_id,
                        "base_state_version": claim.base_state_version,
                    }
                )
                with self.assertRaisesRegex(WorkerFenceError, "TASK_GRAPH_VERSION_FENCED"):
                    scheduler.record_work_result(claim, payload=payload)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
