from __future__ import annotations

import tempfile
import unittest
import datetime as dt
from pathlib import Path

from master_a_dynamic_v4.browser_adapter import BrowserAdapter
from master_a_dynamic_v4.operator_control import OperatorControlService
from master_a_dynamic_v4.runtime_protocol import parse_request
from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError


class _Engine:
    def __init__(self):
        self.auth_calls = 0
        self.submit_calls = 0

    def auth_state(self, channel):
        self.auth_calls += 1
        return {"status": "AUTHENTICATED"}

    def submit(self, intent):
        self.submit_calls += 1
        return {"status": "SUBMITTED", "conversation_url": "https://chatgpt.com/c/x", "remote_identity": "x"}

    def reconcile(self, intent):
        return {"status": "VERIFIED_NOT_SUBMITTED", "proof": "read-only-proof"}


class _ExecutionAdapter:
    def __init__(self):
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        return {"assignment_id": "a1", "task_id": "t1", "exit_code": 0}


class GenerationFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"required": []}
        )
        lease = self.store.acquire_daemon_lease("p1", "daemon")
        self.operator = OperatorControlService(self.store, daemon_epoch=lease["daemon_epoch"])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _pause(self):
        control = self.store.get_project_state("p1")
        lease = self.store.get_operator_control("p1")
        request = parse_request({
            "protocol_version": "scorp.runtime.command/1",
            "request_id": "pause",
            "command": "project.pause",
            "project_id": "p1",
            "expected_state_version": control["state_version"],
            "expected_daemon_epoch": 1,
            "payload": {},
        })
        return self.operator.execute(request)

    def test_paused_generation_blocks_browser_before_submit(self):
        control = self.store.get_operator_control("p1")
        self.store.prepare_intent(
            "p1", "intent-1", actor_id="worker", channel="worker/1", action_kind="CHATGPT_SUBMIT",
            payload={"prompt": "hello", "operator_generation": control["operator_generation"],
                     "objective_generation": control["objective_generation"]},
        )
        self._pause()
        engine = _Engine()
        result = BrowserAdapter(self.store, engine).submit_once("intent-1")
        self.assertEqual("BLOCKED_AMBIGUOUS", result["state"])
        self.assertIn("OPERATOR_GENERATION_FENCED", result["ambiguity_reason"])
        self.assertEqual(0, engine.submit_calls)

    def test_begin_possible_submit_rechecks_generation_in_transaction(self):
        control = self.store.get_operator_control("p1")
        self.store.prepare_intent(
            "p1", "intent-2", actor_id="worker", channel="worker/1", action_kind="LOCAL_EXECUTION",
            payload={"operator_generation": control["operator_generation"],
                     "objective_generation": control["objective_generation"]},
        )
        self._pause()
        with self.assertRaisesRegex(StoreInvariantError, "OPERATOR_GENERATION_FENCED"):
            self.store.begin_possible_submit("intent-2")
        self.assertEqual("PREPARED", self.store.get_intent("intent-2")["state"])

    def test_fenced_worker_assignment_blocks_browser_before_submit(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler

        work = self.root / "work"
        work.mkdir()
        scheduler = Scheduler(self.store, "p1", PathPolicy([self.root]), max_workers=2)
        scheduler.enqueue_graph([
            {
                "task_id": "T1",
                "objective_sha256": "a" * 64,
                "resource_scope": [work / "assigned.csv"],
                "dependencies": [],
            }
        ])
        started = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.timezone.utc)
        claim = scheduler.claim_runnable(master_epoch=0, now=started, lease_seconds=30)[0]
        control = self.store.get_operator_control("p1")
        self.store.prepare_intent(
            "p1",
            "worker-intent-fenced",
            actor_id=claim.worker_id,
            channel=f"worker/{claim.slot_id}",
            action_kind="CHATGPT_WORKER_SUBMIT",
            payload={
                "prompt": "hello",
                "operator_generation": control["operator_generation"],
                "objective_generation": control["objective_generation"],
                "worker_assignment": {
                    "assignment_id": claim.assignment_id,
                    "task_id": claim.task_id,
                    "worker_id": claim.worker_id,
                    "slot_id": claim.slot_id,
                    "master_epoch": claim.master_epoch,
                    "base_state_version": claim.base_state_version,
                    "lease_token": claim.lease_token,
                    "resource_scope": list(claim.resource_scope),
                    "access_mode": claim.access_mode,
                },
            },
        )
        scheduler.recover_expired_leases(now=started + dt.timedelta(seconds=31))
        engine = _Engine()
        result = BrowserAdapter(self.store, engine).submit_once("worker-intent-fenced")
        self.assertEqual("BLOCKED_AMBIGUOUS", result["state"])
        self.assertIn("WORKER", result["ambiguity_reason"])
        self.assertEqual(0, engine.submit_calls)

    def test_paused_generation_blocks_local_execution_adapter(self):
        from master_a_dynamic_v4.master_controller import ControllerRejected, MasterAController
        from master_a_dynamic_v4.scheduler import AssignmentClaim

        class Gateway:
            project_id = "p1"

            def __init__(self, store):
                self.store = store

        root = self.root / "work"
        root.mkdir()
        claim = AssignmentClaim(
            assignment_id="a1", project_id="p1", task_id="t1", worker_id="w1",
            slot_id="worker-slot-1", lease_token="lease-1", master_epoch=0,
            base_state_version=0, objective_sha256="a" * 64, resource_scope=(str(root),),
            access_mode="read", expires_at="2099-01-01T00:00:00Z",
        )
        adapter = _ExecutionAdapter()
        controller = MasterAController(Gateway(self.store), "master-1", execution_adapter=adapter)
        self._pause()
        with self.assertRaises(ControllerRejected):
            controller._execute_request(
                claim,
                {
                    "module": "master_a_dynamic_v4.csv_workload.cli",
                    "args": [], "working_directory": str(root), "resource_paths": [],
                    "access_mode": "read", "timeout_seconds": 10,
                },
            )
        self.assertEqual(0, adapter.calls)


if __name__ == "__main__":
    unittest.main()
