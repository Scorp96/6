from __future__ import annotations

import json
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal


FIXTURE = pathlib.Path(__file__).with_name("fixtures") / "orders.csv"


class CsvWorkloadTests(unittest.TestCase):
    def test_strict_reader_and_decimal_aggregation(self):
        from master_a_dynamic_v4.csv_workload.aggregate import aggregate_by_category
        from master_a_dynamic_v4.csv_workload.reader import read_rows

        rows = read_rows(FIXTURE)
        self.assertEqual(3, len(rows))
        self.assertEqual(Decimal("12.30"), rows[0].amount)
        totals = aggregate_by_category(rows)
        self.assertEqual(["alpha", "beta"], list(totals))
        self.assertEqual(Decimal("12.30"), totals["alpha"])
        self.assertEqual(Decimal("10.00"), totals["beta"])

    def test_malformed_header_decimal_and_empty_input_are_deterministic(self):
        from master_a_dynamic_v4.csv_workload.reader import CsvInputError, read_rows

        cases = {
            "header.csv": ("category,amount\nalpha,1.00\n", "CSV_HEADER_INVALID"),
            "decimal.csv": ("order_id,category,amount\nO-1,alpha,NaN\n", "CSV_AMOUNT_INVALID:2"),
            "empty.csv": ("", "CSV_HEADER_INVALID"),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            for name, (content, code) in cases.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(content, encoding="utf-8", newline="")
                    with self.assertRaisesRegex(CsvInputError, code):
                        read_rows(path)

    def test_stable_report_and_cli_use_unicode_canonical_json_with_newline(self):
        from master_a_dynamic_v4.csv_workload.aggregate import stable_report

        with tempfile.TemporaryDirectory() as td:
            source = pathlib.Path(td) / "unicode.csv"
            source.write_text(
                "order_id,category,amount\nO-1,研发,0.10\nO-2,alpha,2.00\n",
                encoding="utf-8",
                newline="",
            )
            expected = '{"categories":{"alpha":"2.00","研发":"0.10"},"row_count":2,"total":"2.10"}\n'
            self.assertEqual(expected, stable_report(source))
            env = os.environ.copy()
            env["PYTHONPATH"] = str(pathlib.Path(__file__).parents[2])
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "master_a_dynamic_v4.csv_workload.cli", str(source)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(expected, completed.stdout)
            self.assertEqual("", completed.stderr)

    def test_t1_t2_admit_in_parallel_and_t3_waits_for_both_verified_results(self):
        from master_a_dynamic_v4.csv_workload.graph import build_task_graph
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "orders.csv"
            source.write_bytes(FIXTURE.read_bytes())
            output = root / "report.json"
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            store.create_contract(
                "csv-project",
                root_contract={"objective": "real CSV workload"},
                acceptance_contract={"required": ["AC05"]},
            )
            try:
                scheduler = Scheduler(store, "csv-project", PathPolicy([root]), max_workers=2)
                scheduler.enqueue_graph(build_task_graph(source, output))
                first = scheduler.claim_runnable(master_epoch=0)
                self.assertEqual(["T1", "T2"], sorted(claim.task_id for claim in first))
                self.assertEqual("QUEUED", scheduler.get_task("T3")["state"])
                for claim in first:
                    result_id = scheduler.record_candidate(
                        claim.assignment_id,
                        lease_token=claim.lease_token,
                        master_epoch=claim.master_epoch,
                        kind="HANDOFF",
                        payload={"task_id": claim.task_id, "result_sha256": claim.objective_sha256, "real_output_sha256": claim.objective_sha256},
                    )
                    scheduler.verify_candidate(result_id, result_sha256=claim.objective_sha256)
                final = scheduler.claim_runnable(master_epoch=0)
                self.assertEqual(["T3"], [claim.task_id for claim in final])
            finally:
                store.close()

    def test_real_csv_worker_flow_uses_structured_results_and_executes_dependency(self):
        """Exercise the actual workload through the version-bound Worker path."""
        from master_a_dynamic_v4.csv_workload.aggregate import aggregate_by_category, stable_report
        from master_a_dynamic_v4.csv_workload.graph import build_task_graph
        from master_a_dynamic_v4.csv_workload.reader import read_rows
        from master_a_dynamic_v4.models import sha256_json
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore
        from master_a_dynamic_v4.work_result import result_content_sha256

        def digest_bytes(value: bytes) -> str:
            return hashlib.sha256(value).hexdigest()

        def structured(claim, *, scope, deliverables, evidence, acceptance):
            payload = {
                "work_result_version": "1",
                "project_id": "csv-structured-project",
                "worker_id": claim.worker_id,
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "objective_sha256": claim.objective_sha256,
                "base_state_version": claim.base_state_version,
                "candidate_commit": "a" * 40,
                "status": "COMPLETE",
                "scope_completed": scope,
                "scope_not_completed": [],
                "deliverables": deliverables,
                "evidence": evidence,
                "acceptance_coverage": acceptance,
                "facts": ["executed real CSV workload code"],
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
            source.write_bytes(FIXTURE.read_bytes())
            output = root / "report.json"
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            store.create_contract(
                "csv-structured-project",
                root_contract={"objective": "real CSV workload through Worker contract"},
                acceptance_contract={"required": ["AC05"]},
            )
            try:
                scheduler = Scheduler(store, "csv-structured-project", PathPolicy([root]), max_workers=2)
                scheduler.enqueue_graph(build_task_graph(source, output))
                first = scheduler.claim_runnable(master_epoch=0)
                self.assertEqual(["T1", "T2"], sorted(claim.task_id for claim in first))

                rows = read_rows(source)
                totals = aggregate_by_category(rows)
                executions = {
                    "T1": structured(
                        next(claim for claim in first if claim.task_id == "T1"),
                        scope=["strict CSV read"],
                        deliverables=[{"path": str(source), "sha256": digest_bytes(source.read_bytes())}],
                        evidence=[{"row_count": len(rows), "amounts_are_decimal": True}],
                        acceptance=["AC05:T1"],
                    ),
                    "T2": structured(
                        next(claim for claim in first if claim.task_id == "T2"),
                        scope=["Decimal category aggregation"],
                        deliverables=[{"categories": sorted(totals), "total": str(sum(totals.values()))}],
                        evidence=[{"category_count": len(totals), "decimal_total": str(sum(totals.values()))}],
                        acceptance=["AC05:T2"],
                    ),
                }
                for claim in first:
                    result_id = scheduler.record_work_result(claim, payload=executions[claim.task_id])
                    scheduler.verify_candidate(result_id, result_sha256=executions[claim.task_id]["result_sha256"])

                dependent = scheduler.claim_runnable(master_epoch=0)
                self.assertEqual(["T3"], [claim.task_id for claim in dependent])
                report = stable_report(source)
                output.write_text(report, encoding="utf-8", newline="")
                t3 = dependent[0]
                t3_payload = structured(
                    t3,
                    scope=["stable JSON report"],
                    deliverables=[{"path": str(output), "sha256": digest_bytes(output.read_bytes())}],
                    evidence=[{"report": json.loads(report), "newline_terminated": report.endswith("\n")}],
                    acceptance=["AC05:T3"],
                )
                result_id = scheduler.record_work_result(t3, payload=t3_payload)
                scheduler.verify_candidate(result_id, result_sha256=t3_payload["result_sha256"])
                self.assertEqual("ACCEPTED", scheduler.get_task("T1")["state"])
                self.assertEqual("ACCEPTED", scheduler.get_task("T2")["state"])
                self.assertEqual("ACCEPTED", scheduler.get_task("T3")["state"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
