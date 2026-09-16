from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest


class ExecutionAdapterTests(unittest.TestCase):
    def make_claim(self, root: pathlib.Path, *, mode: str = "read"):
        from master_a_dynamic_v4.scheduler import AssignmentClaim

        return AssignmentClaim(
            assignment_id="assignment-exec",
            project_id="execution-project",
            task_id="T1",
            worker_id="worker-exec",
            slot_id="worker-slot-1",
            lease_token="lease-exec",
            master_epoch=0,
            base_state_version=0,
            objective_sha256="a" * 64,
            resource_scope=(str(root / "orders.csv"),),
            access_mode=mode,
            expires_at="2099-01-01T00:00:00Z",
        )

    def test_runs_allowlisted_csv_module_without_shell_and_records_evidence(self):
        from master_a_dynamic_v4.execution_adapter import LocalExecutionAdapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "orders.csv"
            source.write_text(
                "order_id,category,amount\nO-1,alpha,1.20\n",
                encoding="utf-8",
                newline="",
            )
            adapter = LocalExecutionAdapter(
                [root], python_executable=sys.executable, pythonpath=pathlib.Path(__file__).parents[2]
            )
            receipt = adapter.execute(
                self.make_claim(root),
                module="master_a_dynamic_v4.csv_workload.cli",
                args=[str(source)],
                working_directory=root,
                resource_paths=[source],
                access_mode="read",
            )
            self.assertEqual(0, receipt.exit_code)
            self.assertIn('"alpha":"1.20"', receipt.stdout)
            self.assertEqual(64, len(receipt.stdout_sha256))
            self.assertEqual(str(root.resolve()), receipt.working_directory)

    def test_rejects_path_escape_and_non_allowlisted_module_before_process(self):
        from master_a_dynamic_v4.execution_adapter import ExecutionAdapterRejected, LocalExecutionAdapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outside = root.parent / "outside.csv"
            adapter = LocalExecutionAdapter([root], python_executable=sys.executable)
            claim = self.make_claim(root)
            with self.assertRaisesRegex(ExecutionAdapterRejected, "PATH_OUTSIDE_ALLOWLIST"):
                adapter.execute(
                    claim,
                    module="master_a_dynamic_v4.csv_workload.cli",
                    args=[str(outside)],
                    working_directory=root,
                    resource_paths=[outside],
                    access_mode="read",
                )
            with self.assertRaisesRegex(ExecutionAdapterRejected, "MODULE_NOT_ALLOWLISTED"):
                adapter.execute(
                    claim,
                    module="os",
                    args=[],
                    working_directory=root,
                    resource_paths=[root / "orders.csv"],
                    access_mode="read",
                )

    def test_rejects_lease_identity_mismatch_before_process(self):
        from master_a_dynamic_v4.execution_adapter import ExecutionAdapterRejected, LocalExecutionAdapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            adapter = LocalExecutionAdapter([root], python_executable=sys.executable)
            claim = self.make_claim(root)
            with self.assertRaisesRegex(ExecutionAdapterRejected, "CLAIM_ASSIGNMENT_MISMATCH"):
                adapter.execute(
                    claim,
                    module="master_a_dynamic_v4.csv_workload.cli",
                    args=[str(root / "orders.csv")],
                    working_directory=root,
                    resource_paths=[root / "orders.csv"],
                    access_mode="read",
                    expected_assignment_id="different-assignment",
                )

    def test_rejects_same_root_path_outside_assignment_scope(self):
        from master_a_dynamic_v4.execution_adapter import ExecutionAdapterRejected, LocalExecutionAdapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            assigned = root / "assigned.csv"
            unrelated = root / "unrelated.csv"
            assigned.write_text("order_id,category,amount\nO-1,alpha,1.20\n", encoding="utf-8")
            unrelated.write_text("order_id,category,amount\nO-2,beta,2.30\n", encoding="utf-8")
            adapter = LocalExecutionAdapter([root], python_executable=sys.executable, pythonpath=pathlib.Path(__file__).parents[2])
            with self.assertRaisesRegex(ExecutionAdapterRejected, "RESOURCE_OUTSIDE_ASSIGNMENT_SCOPE"):
                adapter.execute(
                    self.make_claim(root),
                    module="master_a_dynamic_v4.csv_workload.cli",
                    args=[str(unrelated)],
                    working_directory=root,
                    resource_paths=[unrelated],
                    access_mode="read",
                )

    def test_rejects_working_directory_outside_assignment_scope(self):
        from master_a_dynamic_v4.execution_adapter import ExecutionAdapterRejected, LocalExecutionAdapter
        from master_a_dynamic_v4.scheduler import AssignmentClaim

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            assigned = root / "assigned"
            other = root / "other"
            assigned.mkdir()
            other.mkdir()
            claim = AssignmentClaim(
                assignment_id="assignment-scope",
                project_id="execution-project",
                task_id="T1",
                worker_id="worker-scope",
                slot_id="worker-slot-1",
                lease_token="lease-scope",
                master_epoch=0,
                base_state_version=0,
                objective_sha256="a" * 64,
                resource_scope=(str(assigned / "assigned.csv"),),
                access_mode="read",
                expires_at="2099-01-01T00:00:00Z",
            )
            adapter = LocalExecutionAdapter([root], python_executable=sys.executable)
            with self.assertRaisesRegex(ExecutionAdapterRejected, "WORKING_DIRECTORY_OUTSIDE_ASSIGNMENT_SCOPE"):
                adapter.execute(
                    claim,
                    module="master_a_dynamic_v4.csv_workload.cli",
                    args=["secret.csv"],
                    working_directory=other,
                    resource_paths=[assigned / "assigned.csv"],
                    access_mode="read",
                )

    def test_rejects_relative_path_argument_even_without_directory_separator(self):
        from master_a_dynamic_v4.execution_adapter import ExecutionAdapterRejected, LocalExecutionAdapter
        from master_a_dynamic_v4.scheduler import AssignmentClaim

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            assigned = root / "assigned"
            assigned.mkdir()
            claim = AssignmentClaim(
                assignment_id="assignment-relative",
                project_id="execution-project",
                task_id="T1",
                worker_id="worker-relative",
                slot_id="worker-slot-1",
                lease_token="lease-relative",
                master_epoch=0,
                base_state_version=0,
                objective_sha256="a" * 64,
                resource_scope=(str(assigned / "assigned.csv"),),
                access_mode="read",
                expires_at="2099-01-01T00:00:00Z",
            )
            adapter = LocalExecutionAdapter([root], python_executable=sys.executable)
            with self.assertRaisesRegex(ExecutionAdapterRejected, "ARGUMENT_OUTSIDE_ASSIGNMENT_SCOPE"):
                adapter.execute(
                    claim,
                    module="master_a_dynamic_v4.csv_workload.cli",
                    args=["secret.csv"],
                    working_directory=assigned,
                    resource_paths=[assigned / "assigned.csv"],
                    access_mode="read",
                )

    def test_rejects_access_mode_downgrade_or_upgrade(self):
        from master_a_dynamic_v4.execution_adapter import ExecutionAdapterRejected, LocalExecutionAdapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "orders.csv"
            source.write_text("order_id,category,amount\nO-1,alpha,1.20\n", encoding="utf-8")
            adapter = LocalExecutionAdapter([root], python_executable=sys.executable, pythonpath=pathlib.Path(__file__).parents[2])
            with self.assertRaisesRegex(ExecutionAdapterRejected, "CLAIM_ACCESS_MODE_MISMATCH"):
                adapter.execute(
                    self.make_claim(root, mode="write"),
                    module="master_a_dynamic_v4.csv_workload.cli",
                    args=[str(source)],
                    working_directory=root,
                    resource_paths=[source],
                    access_mode="read",
                )

    def test_recovery_never_replays_unresolved_local_execution_intent(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter
        from master_a_dynamic_v4.recovery import recover_pending_intents
        from master_a_dynamic_v4.state_store import StateStore

        class Engine:
            def __init__(self):
                self.reconcile_calls = 0

            def reconcile(self, _intent):
                self.reconcile_calls += 1
                return {"status": "RESPONSE_CAPTURED"}

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            engine = Engine()
            try:
                store.create_contract(
                    "local-recovery",
                    root_contract={"objective": "local"},
                    acceptance_contract={"required": ["AC"]},
                )
                store.prepare_intent(
                    "local-recovery",
                    "execution-intent-recovery",
                    actor_id="worker-1",
                    channel="execution/worker-slot-1",
                    action_kind="LOCAL_EXECUTION",
                    payload={"assignment_id": "assignment-recovery", "request": {"module": "x"}},
                )
                store.begin_possible_submit("execution-intent-recovery")
                outcomes = recover_pending_intents(BrowserAdapter(store, engine))
                self.assertEqual(
                    [("execution-intent-recovery", "LOCAL_EXECUTION_RECONCILIATION_REQUIRED")],
                    outcomes,
                )
                self.assertEqual(0, engine.reconcile_calls)
                self.assertEqual(
                    "MAY_HAVE_SUBMITTED",
                    store.get_intent("execution-intent-recovery")["state"],
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
