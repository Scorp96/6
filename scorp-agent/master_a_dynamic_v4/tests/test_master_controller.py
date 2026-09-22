from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import time
import unittest
import datetime as dt
from dataclasses import dataclass


class MissingControllerTests(unittest.TestCase):
    def test_worker_result_hash_normalizes_model_scalar_types_before_storage(self):
        from master_a_dynamic_v4.master_controller import normalize_worker_result_envelope

        payload = {
            "work_result_version": 1,
            "status": "complete",
            "base_state_version": "1",
            "candidate_commit": "A" * 40,
            "objective_sha256": "B" * 64,
            "scope_completed": ("T1",),
        }
        normalized = normalize_worker_result_envelope(payload)
        self.assertEqual("1", normalized["work_result_version"])
        self.assertEqual("COMPLETE", normalized["status"])
        self.assertEqual(1, normalized["base_state_version"])
        self.assertEqual("a" * 40, normalized["candidate_commit"])
        self.assertEqual("b" * 64, normalized["objective_sha256"])
        self.assertEqual(["T1"], normalized["scope_completed"])

    def test_resume_replaces_expired_physical_session_id_in_real_sqlite(self):
        from master_a_dynamic_v4.master_controller import MasterAController
        from master_a_dynamic_v4.state_store import StateStore

        class Gateway:
            project_id = "controller-project"

            def __init__(self, store):
                self.store = store

            def start_master_session(self, session_id):
                return self.store.start_master_session(
                    self.project_id,
                    session_id,
                    now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                    ttl_seconds=60,
                )

            def load_worker_claims(self, *, master_epoch):
                return []

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("controller-project", root_contract={"objective": "resume"}, acceptance_contract={"ids": []})
            store.start_master_session(
                "controller-project",
                "master-a",
                now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                ttl_seconds=1,
            )
            store.inspect_master_session(
                "controller-project",
                now=dt.datetime(2026, 1, 1, 0, 0, 2, tzinfo=dt.timezone.utc),
            )
            controller = MasterAController(Gateway(store), "master-a")
            resumed = controller.resume()
            self.assertNotEqual("master-a", resumed["session_id"])
            self.assertEqual(resumed["session_id"], controller.session_id)
            self.assertEqual(1, resumed["master_epoch"])
            active = store.inspect_master_session(
                "controller-project",
                now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            )
            self.assertEqual("MASTER_ACTIVE", active["status"])

    def test_attach_existing_active_session_adopts_current_epoch_and_session_id(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project")
        gateway.watchdog_once = lambda: {
            "status": "MASTER_ACTIVE",
            "master_epoch": 7,
            "session_id": "master-session::resume-durable",
        }
        controller = MasterAController(gateway, "master-session")
        attached = controller.attach_existing_session()
        self.assertEqual(7, controller.master_epoch)
        self.assertEqual("master-session::resume-durable", controller.session_id)
        self.assertEqual("MASTER_ACTIVE", attached["status"])

    def test_attach_existing_real_sqlite_resumed_session_heartbeats_without_new_epoch(self):
        import datetime as dt
        import pathlib
        import tempfile

        from master_a_dynamic_v4.master_controller import MasterAController
        from master_a_dynamic_v4.master_watchdog import MasterWatchdog
        from master_a_dynamic_v4.state_store import StateStore

        class Gateway:
            project_id = "controller-project"

            def __init__(self, store):
                self.watchdog = MasterWatchdog(store, self.project_id, ttl_seconds=60)

            def watchdog_once(self):
                return self.watchdog.run_once(
                    now=dt.datetime(2026, 1, 1, 0, 0, 10, tzinfo=dt.timezone.utc)
                )

            def heartbeat_master_session(self, session_id, *, master_epoch):
                return self.watchdog.heartbeat(
                    session_id,
                    master_epoch=master_epoch,
                    now=dt.datetime(2026, 1, 1, 0, 0, 10, tzinfo=dt.timezone.utc),
                )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            try:
                store.create_contract(
                    "controller-project",
                    root_contract={"objective": "restart attach"},
                    acceptance_contract={"ids": []},
                )
                started = store.start_master_session(
                    "controller-project",
                    "master-a-production::resume-existing",
                    now=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                    ttl_seconds=60,
                )
                controller = MasterAController(Gateway(store), "master-a-production")
                attached = controller.attach_existing_session()
                self.assertEqual("MASTER_ACTIVE", attached["status"])
                self.assertEqual(started["master_epoch"], controller.master_epoch)
                self.assertEqual(
                    "master-a-production::resume-existing", controller.session_id
                )
                heartbeat = controller.heartbeat()
                self.assertEqual(
                    "master-a-production::resume-existing", heartbeat["session_id"]
                )
                self.assertEqual(started["master_epoch"], heartbeat["master_epoch"])
                state = store.get_project_state("controller-project")
                self.assertEqual(started["master_epoch"], state["master_epoch"])
            finally:
                store.close()

    def test_attach_existing_active_session_without_durable_session_id_fails_closed(self):
        from master_a_dynamic_v4.master_controller import ControllerRejected, MasterAController

        gateway = _FakeGateway("controller-project")
        gateway.watchdog_once = lambda: {"status": "MASTER_ACTIVE", "master_epoch": 7}
        controller = MasterAController(gateway, "master-session")
        with self.assertRaisesRegex(ControllerRejected, "MASTER_SESSION_ID_MISSING"):
            controller.attach_existing_session()

    def test_master_identity_is_validated_before_gateway_calls(self):
        from master_a_dynamic_v4.master_controller import ControllerRejected, MasterAController

        class Gateway:
            project_id = "controller-project"

            def ensure_contract(self, *_args, **_kwargs):
                raise AssertionError("invalid plan must be rejected before gateway use")

        controller = MasterAController(Gateway(), "master-session")
        with self.assertRaisesRegex(ControllerRejected, "MASTER_IDENTITY_INVALID"):
            controller.apply_plan(
                {"project_id": "controller-project", "master_identity": "B", "tasks": []}
            )

    def test_apply_plan_reentry_adopts_existing_transition_without_second_commit(self):
        from master_a_dynamic_v4.master_controller import ControllerRejected, MasterAController
        from master_a_dynamic_v4.models import CommitResult, sha256_json

        class ReplayGateway(_FakeGateway):
            def __init__(self):
                super().__init__("controller-project")
                self.commit_calls = 0
                self.transitions = {}
                self._state_version = 0

            def describe(self):
                return {
                    "state_version": self._state_version,
                    "master_epoch": self._epoch,
                }

            def get_master_transition(self, transition_id):
                row = self.transitions.get(transition_id)
                return None if row is None else dict(row)

            def commit_master_proposal(self, transition_id, proposal, *, expected_version, master_epoch):
                self.commit_calls += 1
                if expected_version != self._state_version:
                    return CommitResult.VERSION_CONFLICT
                self.transitions[transition_id] = {
                    "transition_id": transition_id,
                    "project_id": self.project_id,
                    "master_epoch": master_epoch,
                    "proposal_sha256": sha256_json(proposal),
                    "result": CommitResult.COMMITTED.value,
                }
                self._state_version += 1
                return CommitResult.COMMITTED

        gateway = ReplayGateway()
        plan = {
            "project_id": "controller-project",
            "master_identity": "A",
            "transition_id": "durable-plan",
            "tasks": [_task("T1", "a" * 64)],
        }

        first = MasterAController(gateway, "master-session")
        first.start({"objective": "reentry"}, {"required": ["AC_CONTROLLER"]})
        first_admission = first.apply_plan(plan)
        self.assertEqual(1, gateway.commit_calls)
        self.assertEqual(1, gateway._state_version)

        # Simulate unrelated durable state progress after the plan commit.
        gateway._state_version = 7

        resumed = MasterAController(gateway, "master-session")
        resumed.start({"objective": "reentry"}, {"required": ["AC_CONTROLLER"]})
        replay_admission = resumed.apply_plan(plan)

        self.assertEqual(1, gateway.commit_calls)
        self.assertEqual(first_admission["plan_sha256"], replay_admission["plan_sha256"])
        self.assertEqual(("T1",), replay_admission["task_ids"])

        changed = {
            **plan,
            "tasks": [_task("T1", "b" * 64)],
        }
        with self.assertRaisesRegex(ControllerRejected, "MASTER_PLAN_TRANSITION_CONFLICT"):
            resumed.apply_plan(changed)
        self.assertEqual(1, gateway.commit_calls)

    def test_controller_step_reports_two_distinct_assignments(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project")
        controller = MasterAController(gateway, "master-session")
        controller.start(
            {"objective": "two workers"},
            {"required": ["AC_CONTROLLER"]},
        )
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [
                    _task("T1", "a" * 64),
                    _task("T2", "b" * 64),
                    _task("T3", "c" * 64, dependencies=["T1", "T2"]),
                ],
            }
        )
        step = controller.step(
            lambda claim: f"complete {claim.task_id}",
            lambda row: _result_for(row),
        )
        self.assertEqual("DISPATCHED", step.status)
        self.assertEqual(2, len(step.outcomes))
        self.assertEqual(2, len({item["assignment_id"] for item in step.outcomes}))
        self.assertEqual({"T1", "T2"}, {item["task_id"] for item in step.outcomes})
        self.assertEqual(2, len(gateway.claimed))
        self.assertEqual(2, len(gateway.verified))

    def test_controller_enters_two_browser_dispatches_before_either_finishes(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _ConcurrentFakeGateway("controller-project")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "parallel browser dispatch"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64), _task("T2", "b" * 64)],
            }
        )

        step = controller.step(lambda claim: f"complete {claim.task_id}", lambda row: _result_for(row))

        self.assertEqual("DISPATCHED", step.status)
        self.assertEqual(2, gateway.submit_calls)
        self.assertTrue(gateway.both_submits_entered)

    def test_controller_heartbeats_master_while_slow_dispatch_is_in_flight(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _HeartbeatSlowGateway("controller-project")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "keep master lease live"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )

        step = controller.step(lambda claim: f"complete {claim.task_id}", lambda row: _result_for(row))

        self.assertEqual("DISPATCHED", step.status)
        self.assertGreaterEqual(gateway.heartbeat_calls, 1)

    def test_lost_master_heartbeat_fences_slow_dispatch_before_result_admission(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FailingHeartbeatSlowGateway("controller-project")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "fence stale master"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )

        step = controller.step(lambda claim: f"complete {claim.task_id}", lambda row: _result_for(row))

        self.assertEqual("BLOCKED", step.status)
        self.assertEqual([], gateway.verified)
        self.assertTrue(any("MASTER_HEARTBEAT_FAILED" in item for item in step.blockers))

    def test_worker_transport_exception_becomes_blocked_outcome(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FailingSubmitGateway("controller-project")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "transport failure"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )

        step = controller.step(lambda claim: f"complete {claim.task_id}", lambda row: _result_for(row))

        self.assertEqual("BLOCKED", step.status)
        self.assertEqual((), step.outcomes)
        self.assertEqual(("T1:DISPATCH_EXCEPTION:RuntimeError",), step.blockers)

    def test_audit_only_complete_result_recomputes_hash_without_local_execution(self):
        from master_a_dynamic_v4.master_controller import MasterAController
        from master_a_dynamic_v4.work_result import result_content_sha256

        gateway = _FakeGateway("controller-project")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "audit only"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )

        def decode(row):
            result = _result_for(row)
            result["result_sha256"] = "0" * 64
            result.pop("execution_request", None)
            return result

        step = controller.step(lambda claim: "audit bounded evidence", decode)
        self.assertEqual("DISPATCHED", step.status)
        self.assertEqual(1, len(gateway.verified))
        stored = gateway.verified[0][1]
        self.assertNotEqual("0" * 64, stored["result_sha256"])
        self.assertEqual(result_content_sha256(stored), stored["result_sha256"])
        self.assertNotIn("execution_request", stored)

    def test_controller_routes_structured_execution_request_through_adapter(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project")
        adapter = _FakeExecutionAdapter()
        worktree_manager = _FakeGitWorktreeManager()
        controller = MasterAController(
            gateway,
            "master-session",
            execution_adapter=adapter,
            git_worktree_manager=worktree_manager,
        )
        controller.start({"objective": "execute bounded local work"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )

        def decode(row):
            result = _result_for(row)
            result["execution_request"] = {
                "module": "master_a_dynamic_v4.csv_workload.cli",
                "args": [],
                "working_directory": "C:/lab",
                "resource_paths": ["C:/lab/T1.txt"],
                "access_mode": "write",
                "timeout_seconds": 5,
                "repository": "C:/lab/repository",
                "worktree": "C:/lab/T1-worktree",
                "base_commit": "a" * 40,
            }
            return result

        step = controller.step(lambda claim: "run bounded task", decode)
        self.assertEqual("DISPATCHED", step.status)
        self.assertEqual(1, len(adapter.calls))
        self.assertTrue(all("execution_receipt" in payload for _, payload in gateway.verified))
        self.assertEqual("master_a_dynamic_v4.csv_workload.cli", adapter.calls[0]["module"])
        self.assertEqual(1, len(worktree_manager.calls))

    def test_foreign_complete_result_is_rejected_before_local_execution(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project")
        adapter = _FakeExecutionAdapter()
        controller = MasterAController(
            gateway,
            "master-session",
            execution_adapter=adapter,
            git_worktree_manager=_FakeGitWorktreeManager(),
        )
        controller.start({"objective": "authority before side effect"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )

        def decode(row):
            result = _result_for(row)
            result["project_id"] = "foreign-project"
            result["execution_request"] = {
                "module": "master_a_dynamic_v4.csv_workload.cli",
                "args": [],
                "working_directory": "C:/lab",
                "resource_paths": ["C:/lab/T1.txt"],
                "access_mode": "write",
                "timeout_seconds": 5,
                "repository": "C:/lab/repository",
                "worktree": "C:/lab/T1-worktree",
                "base_commit": "a" * 40,
            }
            return result

        step = controller.step(lambda claim: "run bounded task", decode)

        self.assertEqual("BLOCKED", step.status)
        self.assertEqual([], adapter.calls)
        self.assertEqual([], gateway.verified)
        self.assertTrue(any("PROJECT_ID_MISMATCH" in blocker for blocker in step.blockers))

    def test_preexecution_authority_gate_rejects_each_assignment_identity_mismatch(self):
        from types import SimpleNamespace
        from master_a_dynamic_v4.master_controller import ControllerRejected, MasterAController

        candidate = "c" * 40
        claim = SimpleNamespace(
            project_id="controller-project",
            assignment_id="assignment-T1",
            task_id="T1",
            worker_id="worker-T1",
            slot_id="worker-slot-1",
            master_epoch=7,
            base_state_version=3,
            objective_sha256="a" * 64,
            task_context={"candidate_commit": candidate},
        )
        base = {
            "work_result_version": "1",
            "project_id": claim.project_id,
            "assignment_id": claim.assignment_id,
            "task_id": claim.task_id,
            "worker_id": claim.worker_id,
            "slot_id": claim.slot_id,
            "master_epoch": 2,
            "base_state_version": claim.base_state_version,
            "objective_sha256": claim.objective_sha256,
            "candidate_commit": candidate,
            "status": "COMPLETE",
        }
        cases = (
            ("project_id", "foreign-project", "PROJECT_ID_MISMATCH"),
            ("assignment_id", "assignment-other", "ASSIGNMENT_ID_MISMATCH"),
            ("task_id", "T9", "TASK_ID_MISMATCH"),
            ("worker_id", "worker-other", "WORKER_ID_MISMATCH"),
            ("slot_id", "worker-slot-2", "SLOT_ID_MISMATCH"),
            ("objective_sha256", "b" * 64, "OBJECTIVE_HASH_MISMATCH"),
            ("base_state_version", 4, "BASE_STATE_VERSION_MISMATCH"),
            ("candidate_commit", "d" * 40, "CANDIDATE_COMMIT_MISMATCH"),
        )
        for field, bad_value, error in cases:
            with self.subTest(field=field):
                payload = dict(base)
                payload[field] = bad_value
                with self.assertRaisesRegex(ControllerRejected, error):
                    MasterAController._validate_preexecution_worker_authority(claim, payload)

    def test_preexecution_authority_gate_allows_prior_master_epoch_capture(self):
        from types import SimpleNamespace
        from master_a_dynamic_v4.master_controller import MasterAController

        candidate = "c" * 40
        claim = SimpleNamespace(
            project_id="controller-project", assignment_id="assignment-T1", task_id="T1",
            worker_id="worker-T1", slot_id="worker-slot-1", master_epoch=7, base_state_version=3,
            objective_sha256="a" * 64, task_context={"candidate_commit": candidate},
        )
        payload = {
            "work_result_version": "1", "project_id": claim.project_id,
            "assignment_id": claim.assignment_id, "task_id": claim.task_id,
            "worker_id": claim.worker_id, "slot_id": claim.slot_id,
            "master_epoch": 2, "base_state_version": claim.base_state_version,
            "objective_sha256": claim.objective_sha256, "candidate_commit": candidate,
            "status": "COMPLETE",
        }
        MasterAController._validate_preexecution_worker_authority(claim, payload)

    def test_blocked_worker_result_never_executes_its_request(self):
        from master_a_dynamic_v4.master_controller import MasterAController
        from master_a_dynamic_v4.work_result import result_content_sha256

        gateway = _FakeGateway("controller-project")
        adapter = _FakeExecutionAdapter()
        worktree_manager = _FakeGitWorktreeManager()
        controller = MasterAController(
            gateway,
            "master-session",
            execution_adapter=adapter,
            git_worktree_manager=worktree_manager,
        )
        controller.start({"objective": "blocked worker"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )

        def decode(row):
            result = _result_for(row)
            result.update(
                {
                    "status": "BLOCKED",
                    "scope_completed": [],
                    "evidence": [],
                    "acceptance_coverage": [],
                    "execution_request": {
                        "module": "master_a_dynamic_v4.csv_workload.cli",
                        "args": [],
                        "working_directory": "C:/lab",
                        "resource_paths": ["C:/lab/T1.txt"],
                        "access_mode": "write",
                        "timeout_seconds": 5,
                        "repository": "C:/lab/repository",
                        "worktree": "C:/lab/T1-worktree",
                        "base_commit": "a" * 40,
                    },
                }
            )
            result["result_sha256"] = "0" * 64
            return result

        step = controller.step(lambda claim: "blocked bounded task", decode)
        self.assertEqual("BLOCKED", step.status)
        self.assertEqual([], adapter.calls)
        self.assertEqual([], worktree_manager.calls)
        self.assertEqual("BLOCKED", gateway.verified[0][1]["status"])

    def test_expired_worker_fencing_precedes_captured_response_recovery(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        class RecoveryOrderGateway(_FakeGateway):
            def __init__(self):
                super().__init__("controller-project")
                self.recovery_order = []
                self.expiration_pass_seen = False

            def load_worker_claims(self, *, master_epoch):
                self.recovery_order.append("load")
                self.expiration_pass_seen = True
                return []

            def recover_captured_response_claims(self, *, master_epoch):
                self.recovery_order.append("recover")
                if not self.expiration_pass_seen:
                    raise AssertionError("RECOVERY_RAN_BEFORE_EXPIRED_LEASE_FENCING")
                return []

            def claim_workers(self, *, master_epoch, limit=2):
                return []

        gateway = RecoveryOrderGateway()
        controller = MasterAController(gateway, "master-session")
        controller.start(
            {"objective": "recovery ordering"},
            {"required": ["AC_CONTROLLER"]},
        )
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )
        step = controller.step(lambda claim: "must not dispatch")
        self.assertEqual("IDLE", step.status)
        self.assertEqual(["load", "recover", "load"], gateway.recovery_order)
        self.assertEqual((), step.claims)

    def test_ambiguous_browser_state_is_left_for_reconciliation(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project", submit_state="MAY_HAVE_SUBMITTED")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "ambiguous"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )
        step = controller.step(lambda claim: "do bounded work", lambda row: {})
        self.assertEqual("BLOCKED", step.status)
        self.assertTrue(any("BROWSER_RECONCILIATION_REQUIRED" in item for item in step.blockers))
        self.assertEqual([], gateway.verified)

    def test_captured_local_execution_receipt_finishes_outbox_without_reexecution(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from master_a_dynamic_v4.master_controller import ControllerRejected, MasterAController
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract(
                "controller-project",
                root_contract={"objective": "local execution cleanup"},
                acceptance_contract={"ids": []},
            )

            class Gateway:
                project_id = "controller-project"

                def __init__(self, state_store):
                    self.store = state_store

            from master_a_dynamic_v4.path_policy import PathPolicy
            from master_a_dynamic_v4.scheduler import Scheduler

            scheduler = Scheduler(store, "controller-project", PathPolicy([root]), max_workers=2)
            scheduler.enqueue_graph([{
                "task_id": "T1",
                "objective_sha256": "a" * 64,
                "resource_scope": [str(root)],
                "access_mode": "read",
                "dependencies": [],
            }])
            claim = scheduler.claim_runnable(master_epoch=0, limit=1)[0]
            adapter = _FakeExecutionAdapter()
            controller = MasterAController(Gateway(store), "master-local-cleanup", execution_adapter=adapter)
            request = {
                "module": "master_a_dynamic_v4.csv_workload.cli",
                "args": ["input.csv", "--operation", "validate"],
                "working_directory": str(root),
                "resource_paths": [str(root / "input.csv")],
                "access_mode": "read",
                "timeout_seconds": 60,
            }
            try:
                # Simulate a process crash/failure after durable receipt capture
                # but before outbox cleanup.
                with patch.object(store, "finalize_intent", side_effect=RuntimeError("CRASH_AFTER_CAPTURE")):
                    with self.assertRaisesRegex(ControllerRejected, "LOCAL_EXECUTION_CAPTURE_FAILED"):
                        controller._execute_request(claim, request)
                self.assertEqual(1, len(adapter.calls))
                intent_id = f"execution-intent-{claim.assignment_id}"
                self.assertEqual("RESPONSE_CAPTURED", store.get_intent(intent_id)["state"])
                self.assertEqual("PENDING_CLEANUP", store.get_outbox_for_intent(intent_id)["state"])

                # Restart/retry consumes the captured receipt and only finishes
                # cleanup.  It must not invoke the adapter a second time.
                receipt = controller._execute_request(claim, request)
                self.assertEqual(0, receipt["exit_code"])
                self.assertEqual(1, len(adapter.calls))
                self.assertEqual("COMPLETED", store.get_outbox_for_intent(intent_id)["state"])
            finally:
                store.close()

    def test_ambiguous_local_execution_is_never_executed_twice(self):
        from types import SimpleNamespace

        from master_a_dynamic_v4.master_controller import ControllerRejected, MasterAController
        from master_a_dynamic_v4.state_store import StateStore

        class FailingAdapter:
            def __init__(self):
                self.calls = 0

            def execute(self, _claim, **_request):
                self.calls += 1
                raise RuntimeError("MAY_HAVE_EXECUTED")

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract(
                "controller-project",
                root_contract={"objective": "local execution no retry"},
                acceptance_contract={"ids": []},
            )

            class Gateway:
                project_id = "controller-project"

                def __init__(self, state_store):
                    self.store = state_store

            from master_a_dynamic_v4.path_policy import PathPolicy
            from master_a_dynamic_v4.scheduler import Scheduler

            scheduler = Scheduler(store, "controller-project", PathPolicy([root]), max_workers=2)
            scheduler.enqueue_graph([{
                "task_id": "T1",
                "objective_sha256": "b" * 64,
                "resource_scope": [str(root)],
                "access_mode": "read",
                "dependencies": [],
            }])
            claim = scheduler.claim_runnable(master_epoch=0, limit=1)[0]
            adapter = FailingAdapter()
            controller = MasterAController(Gateway(store), "master-local-ambiguous", execution_adapter=adapter)
            request = {
                "module": "master_a_dynamic_v4.csv_workload.cli",
                "args": ["input.csv", "--operation", "validate"],
                "working_directory": str(root),
                "resource_paths": [str(root / "input.csv")],
                "access_mode": "read",
                "timeout_seconds": 60,
            }
            try:
                with self.assertRaisesRegex(ControllerRejected, "LOCAL_EXECUTION_EXCEPTION"):
                    controller._execute_request(claim, request)
                self.assertEqual(1, adapter.calls)
                intent_id = f"execution-intent-{claim.assignment_id}"
                self.assertEqual("BLOCKED_AMBIGUOUS", store.get_intent(intent_id)["state"])

                with self.assertRaisesRegex(ControllerRejected, "LOCAL_EXECUTION_RECONCILIATION_REQUIRED"):
                    controller._execute_request(claim, request)
                self.assertEqual(1, adapter.calls)
            finally:
                store.close()

    def test_restart_does_not_resubmit_an_existing_ambiguous_intent(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project", submit_state="MAY_HAVE_SUBMITTED")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "restart"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )
        first = controller.step(lambda claim: "do bounded work", lambda row: {})
        self.assertEqual("BLOCKED", first.status)
        calls_after_first = gateway.submit_calls
        resumed = MasterAController(gateway, "master-session")
        resumed.master_epoch = controller.master_epoch
        second = resumed.step(lambda claim: "do bounded work", lambda row: {})
        self.assertEqual("BLOCKED", second.status)
        self.assertEqual(calls_after_first, gateway.submit_calls)

    def test_run_cycles_stops_at_a_durable_blocker(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project", submit_state="MAY_HAVE_SUBMITTED")
        controller = MasterAController(gateway, "master-session")
        controller.start({"objective": "bounded loop"}, {"required": ["AC_CONTROLLER"]})
        controller.apply_plan(
            {
                "project_id": "controller-project",
                "master_identity": "A",
                "tasks": [_task("T1", "a" * 64)],
            }
        )
        history = controller.run_cycles(
            lambda claim: "do bounded work",
            lambda row: {},
            max_cycles=4,
        )
        self.assertEqual(1, len(history))
        self.assertEqual("BLOCKED", history[0].status)


def _task(task_id: str, objective_sha256: str, dependencies: list[str] | None = None):
    return {
        "task_id": task_id,
        "objective_sha256": objective_sha256,
        "resource_scope": [f"C:/lab/{task_id}.txt"],
        "access_mode": "write",
        "dependencies": dependencies or [],
        "acceptance_criteria_ids": ["AC_CONTROLLER"],
    }


@dataclass(frozen=True)
class _Claim:
    assignment_id: str
    project_id: str
    task_id: str
    worker_id: str
    slot_id: str
    lease_token: str
    master_epoch: int
    base_state_version: int
    objective_sha256: str
    resource_scope: tuple[str, ...]
    access_mode: str
    expires_at: str = "2099-01-01T00:00:00Z"


class _FakeStore:
    def __init__(self, gateway):
        self.gateway = gateway
        self.local_intents = {}

    def get_intent(self, intent_id):
        return self.gateway.intents[intent_id]

    def prepare_intent(self, project_id, intent_id, *, actor_id, channel, action_kind, payload):
        row = self.local_intents.setdefault(
            intent_id,
            {
                "intent_id": intent_id,
                "project_id": project_id,
                "actor_id": actor_id,
                "channel": channel,
                "action_kind": action_kind,
                "state": "PREPARED",
                "response_json": None,
            },
        )
        return row

    def begin_possible_submit(self, intent_id):
        self.local_intents[intent_id]["state"] = "MAY_HAVE_SUBMITTED"
        return self.local_intents[intent_id]

    def capture_local_execution(self, intent_id, *, receipt, observation):
        self.local_intents[intent_id]["state"] = "RESPONSE_CAPTURED"
        self.local_intents[intent_id]["response_json"] = json.dumps(receipt)
        return self.local_intents[intent_id]

    def finalize_intent(self, intent_id):
        self.local_intents[intent_id]["state"] = "COMPLETED"


class _FakeGateway:
    def __init__(self, project_id: str, *, submit_state: str = "RESPONSE_CAPTURED"):
        self.project_id = project_id
        self.submit_state = submit_state
        self.store = _FakeStore(self)
        self.claimed = []
        self.verified = []
        self.intents = {}
        self._epoch = 1
        self._claims = []
        self.submit_calls = 0

    def ensure_contract(self, root_contract, acceptance_contract):
        self.contract = (dict(root_contract), dict(acceptance_contract))
        return {"contract_sha256": "f" * 64}

    def start_master_session(self, session_id):
        return {"session_id": session_id, "master_epoch": self._epoch, "state": "ACTIVE"}

    def commit_master_proposal(self, *args, **kwargs):
        return "COMMITTED"

    def describe(self):
        return {"state_version": 0, "master_epoch": self._epoch}

    def enqueue_graph(self, tasks):
        self.graph = [dict(task) for task in tasks]
        self._claims = [
            _Claim(
                assignment_id=f"assignment-{task['task_id']}",
                project_id=self.project_id,
                task_id=task["task_id"],
                worker_id=f"worker-{task['task_id']}",
                slot_id=f"worker-slot-{index}",
                lease_token=f"lease-{task['task_id']}",
                master_epoch=self._epoch,
                base_state_version=1,
                objective_sha256=task["objective_sha256"],
                resource_scope=tuple(task["resource_scope"]),
                access_mode=task["access_mode"],
            )
            for index, task in enumerate(tasks[:2], 1)
        ]

    def load_worker_claims(self, *, master_epoch):
        return []

    def claim_workers(self, *, master_epoch, limit=2):
        self.claimed.extend(self._claims[:limit])
        return self._claims[:limit]

    def prepare_worker_intent(self, claim, prompt, *, metadata=None):
        intent_id = f"worker-intent-{claim.assignment_id}"
        if intent_id in self.intents:
            return self.intents[intent_id]
        response = _result_for_claim(claim)
        self.intents[intent_id] = {
            "intent_id": intent_id,
            "state": "PREPARED",
            "response_json": json.dumps(response),
            "assignment_id": claim.assignment_id,
        }
        return self.intents[intent_id]

    def submit_intent(self, intent_id):
        self.submit_calls += 1
        self.intents[intent_id]["state"] = self.submit_state
        return self.intents[intent_id]

    def record_structured_worker_result(self, claim, *, payload):
        self.verified.append((claim, dict(payload)))
        return f"result-{claim.assignment_id}"

    def verify_worker_result(self, result_id, *, result_sha256):
        return None

    def watchdog_once(self):
        return {"status": "MASTER_ACTIVE"}

    def evaluate_completion(self, *, candidate_commit, artifact_hashes):
        from master_a_dynamic_v4.acceptance import AcceptanceDecision
        from master_a_dynamic_v4.models import AcceptanceStatus

        return AcceptanceDecision(AcceptanceStatus.BLOCKED, ("MISSING_EVIDENCE:AC_CONTROLLER",))


class _ConcurrentFakeGateway(_FakeGateway):
    """Make a serial controller fail so the test proves overlap, not count."""

    def __init__(self, project_id):
        super().__init__(project_id)
        self._submit_lock = threading.Lock()
        self._submit_entered = threading.Event()
        self.both_submits_entered = False
        self._submit_count = 0

    def submit_intent(self, intent_id):
        with self._submit_lock:
            self._submit_count += 1
            if self._submit_count == 2:
                self.both_submits_entered = True
                self._submit_entered.set()
        if not self._submit_entered.wait(timeout=1.0):
            raise RuntimeError("DISPATCH_DID_NOT_OVERLAP")
        return super().submit_intent(intent_id)


class _HeartbeatSlowGateway(_FakeGateway):
    def __init__(self, project_id):
        super().__init__(project_id)
        self.heartbeat_calls = 0

    def master_heartbeat_interval_seconds(self):
        return 0.01

    def heartbeat_master_session(self, session_id, *, master_epoch):
        self.heartbeat_calls += 1
        return {"session_id": session_id, "master_epoch": master_epoch, "state": "ACTIVE"}

    def submit_intent(self, intent_id):
        time.sleep(0.05)
        return super().submit_intent(intent_id)


class _FailingHeartbeatSlowGateway(_HeartbeatSlowGateway):
    def heartbeat_master_session(self, session_id, *, master_epoch):
        self.heartbeat_calls += 1
        raise RuntimeError("simulated master lease loss")


class _FailingSubmitGateway(_FakeGateway):
    def submit_intent(self, intent_id):
        raise RuntimeError("simulated transport failure")


class _FakeExecutionReceipt:
    exit_code = 0

    def __init__(self, assignment_id, task_id):
        self.assignment_id = assignment_id
        self.task_id = task_id

    def as_dict(self):
        return {
            "assignment_id": self.assignment_id,
            "task_id": self.task_id,
            "command": ["python", "-m", "module"],
            "working_directory": "C:/lab",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
            "artifact_sha256": {},
            "started_at": "2026-09-14T00:00:00Z",
            "finished_at": "2026-09-14T00:00:01Z",
        }


class _FakeExecutionAdapter:
    def __init__(self):
        self.calls = []

    def execute(self, claim, **request):
        self.calls.append(dict(request))
        return _FakeExecutionReceipt(claim.assignment_id, claim.task_id)


class _FakeGitWorktreeReceipt:
    def as_dict(self):
        return {
            "assignment_id": "assignment-T1",
            "task_id": "T1",
            "repository": "C:/lab/repository",
            "worktree": "C:/lab/T1-worktree",
            "base_commit": "a" * 40,
            "head_commit": "a" * 40,
            "commands": [],
            "stdout_sha256": "c" * 64,
            "stderr_sha256": "d" * 64,
            "started_at": "2026-09-14T00:00:00Z",
            "finished_at": "2026-09-14T00:00:01Z",
        }


class _FakeGitWorktreeManager:
    def __init__(self):
        self.calls = []

    def prepare(self, claim, **request):
        self.calls.append(dict(request))
        return _FakeGitWorktreeReceipt()


def _result_for_claim(claim):
    from master_a_dynamic_v4.work_result import result_content_sha256

    result = {
        "work_result_version": "1",
        "project_id": claim.project_id,
        "worker_id": claim.worker_id,
        "assignment_id": claim.assignment_id,
        "task_id": claim.task_id,
        "objective_sha256": claim.objective_sha256,
        "base_state_version": claim.base_state_version,
        "candidate_commit": "a" * 40,
        "status": "COMPLETE",
        "scope_completed": [claim.task_id],
        "scope_not_completed": [],
        "deliverables": [{"path": f"{claim.task_id}.txt"}],
        "evidence": [{"kind": "unit", "task": claim.task_id}],
        "acceptance_coverage": ["AC_CONTROLLER"],
        "facts": ["bounded result"],
        "inferences": [],
        "unknowns": [],
        "contradictions": [],
        "followup_proposals": [],
    }
    result["result_sha256"] = result_content_sha256(result)
    return result


def _result_for(intent_row):
    return json.loads(intent_row["response_json"])


if __name__ == "__main__":
    unittest.main()
