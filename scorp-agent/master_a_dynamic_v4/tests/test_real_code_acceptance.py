from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest


FIXTURE = pathlib.Path(__file__).with_name("fixtures") / "orders.csv"
CANDIDATE = "a" * 40


class RealCodeAcceptanceTests(unittest.TestCase):
    def test_csv_workload_reaches_independent_completion_gate(self):
        from master_a_dynamic_v4.acceptance import AcceptanceValidator
        from master_a_dynamic_v4.csv_workload.aggregate import aggregate_by_category, stable_report
        from master_a_dynamic_v4.csv_workload.graph import build_task_graph
        from master_a_dynamic_v4.csv_workload.reader import read_rows
        from master_a_dynamic_v4.models import sha256_json
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore
        from master_a_dynamic_v4.work_result import result_content_sha256

        def file_hash(path: pathlib.Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def worker_payload(claim, *, scope, deliverables, evidence):
            payload = {
                "work_result_version": "1",
                "project_id": "csv-acceptance-project",
                "worker_id": claim.worker_id,
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "objective_sha256": claim.objective_sha256,
                "base_state_version": claim.base_state_version,
                "candidate_commit": CANDIDATE,
                "status": "COMPLETE",
                "scope_completed": scope,
                "scope_not_completed": [],
                "deliverables": deliverables,
                "evidence": evidence,
                "acceptance_coverage": ["AC_REAL_CSV"],
                "facts": ["the task executed the repository CSV workload"],
                "inferences": [],
                "unknowns": [],
                "contradictions": [],
                "followup_proposals": [],
            }
            payload["result_sha256"] = result_content_sha256(payload)
            return payload

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "orders.csv"
            report = root / "report.json"
            source.write_bytes(FIXTURE.read_bytes())
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            contract = store.create_contract(
                "csv-acceptance-project",
                root_contract={"objective": "execute the real CSV workload"},
                acceptance_contract={"required": ["AC_REAL_CSV"]},
            )
            try:
                scheduler = Scheduler(store, "csv-acceptance-project", PathPolicy([root]), max_workers=2)
                scheduler.enqueue_graph(build_task_graph(source, report))
                claims = scheduler.claim_runnable(master_epoch=0)
                rows = read_rows(source)
                totals = aggregate_by_category(rows)
                outputs = {
                    "T1": worker_payload(
                        next(c for c in claims if c.task_id == "T1"),
                        scope=["strict CSV read"],
                        deliverables=[{"path": str(source), "sha256": file_hash(source)}],
                        evidence=[{"row_count": len(rows)}],
                    ),
                    "T2": worker_payload(
                        next(c for c in claims if c.task_id == "T2"),
                        scope=["Decimal category aggregation"],
                        deliverables=[{"categories": sorted(totals), "total": str(sum(totals.values()))}],
                        evidence=[{"category_count": len(totals)}],
                    ),
                }
                for claim in claims:
                    result_id = scheduler.record_work_result(claim, payload=outputs[claim.task_id])
                    scheduler.verify_candidate(result_id, result_sha256=outputs[claim.task_id]["result_sha256"], independent_verifier="test-independent-validator")

                t3 = scheduler.claim_runnable(master_epoch=0)[0]
                report.write_text(stable_report(source), encoding="utf-8", newline="")
                t3_payload = worker_payload(
                    t3,
                    scope=["stable JSON report"],
                    deliverables=[{"path": str(report), "sha256": file_hash(report)}],
                    evidence=[{"report": json.loads(report.read_text(encoding="utf-8"))}],
                )
                result_id = scheduler.record_work_result(t3, payload=t3_payload)
                scheduler.verify_candidate(result_id, result_sha256=t3_payload["result_sha256"], independent_verifier="test-independent-validator")

                artifact_hash = file_hash(report)
                store.record_release_candidate(
                    "csv-acceptance-project",
                    candidate_commit=CANDIDATE,
                    manifest={"files": {"report.json": artifact_hash}},
                )
                store.record_evidence_receipt(
                    "csv-acceptance-project",
                    "evidence-real-csv",
                    acceptance_id="AC_REAL_CSV",
                    result="PASS",
                    candidate_commit=CANDIDATE,
                    contract_sha256=contract["contract_sha256"],
                    artifact_sha256=artifact_hash,
                    command_or_action="python -B -m unittest test_real_code_acceptance.py",
                    observed_state={"exit_code": 0, "tasks": ["T1", "T2", "T3"], "workers": 2},
                    raw_output_reference="test-output://real-code-acceptance",
                    started_at="2026-09-14T10:00:00Z",
                    finished_at="2026-09-14T10:00:01Z",
                )
                decision = AcceptanceValidator(store).evaluate(
                    "csv-acceptance-project", CANDIDATE, {"report.json": artifact_hash}
                )
                self.assertEqual("PASS", decision.status.value)
                self.assertEqual((), decision.blockers)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
