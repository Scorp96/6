from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from dataclasses import dataclass


class MissingControllerTests(unittest.TestCase):
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

    def test_controller_routes_structured_execution_request_through_adapter(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        gateway = _FakeGateway("controller-project")
        adapter = _FakeExecutionAdapter()
        controller = MasterAController(gateway, "master-session", execution_adapter=adapter)
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
            }
            return result

        step = controller.step(lambda claim: "run bounded task", decode)
        self.assertEqual("DISPATCHED", step.status)
        self.assertEqual(1, len(adapter.calls))
        self.assertTrue(all("execution_receipt" in payload for _, payload in gateway.verified))
        self.assertEqual("master_a_dynamic_v4.csv_workload.cli", adapter.calls[0]["module"])

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

    def get_intent(self, intent_id):
        return self.gateway.intents[intent_id]


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


class _FakeExecutionReceipt:
    exit_code = 0

    def as_dict(self):
        return {
            "assignment_id": "assignment-placeholder",
            "task_id": "T1",
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
        return _FakeExecutionReceipt()


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
