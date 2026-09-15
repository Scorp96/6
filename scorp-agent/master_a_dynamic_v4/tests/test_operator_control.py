from __future__ import annotations

import tempfile
import unittest
import datetime as dt
from pathlib import Path

from master_a_dynamic_v4.operator_control import OperatorControlService
from master_a_dynamic_v4.runtime_protocol import parse_request
from master_a_dynamic_v4.state_store import StateStore


class OperatorControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"required": []}
        )
        self.lease = self.store.acquire_daemon_lease("p1", "daemon-1")
        self.service = OperatorControlService(
            self.store, daemon_epoch=self.lease["daemon_epoch"], actor="operator"
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def request(self, request_id, command, state_version=0, daemon_epoch=None, payload=None):
        return parse_request(
            {
                "protocol_version": "scorp.runtime.command/1",
                "request_id": request_id,
                "command": command,
                "project_id": "p1",
                "expected_state_version": state_version,
                "expected_daemon_epoch": self.lease["daemon_epoch"] if daemon_epoch is None else daemon_epoch,
                "payload": payload or {},
            }
        )

    def test_pause_is_cas_bound_idempotent_and_durable(self):
        request = self.request("pause-1", "project.pause")
        first = self.service.execute(request)
        self.assertEqual("OK", first["status"])
        self.assertEqual(1, first["state_version"])
        self.assertEqual("PAUSED", self.store.get_operator_control("p1")["operator_state"])
        self.assertEqual("operator", self.store.get_runtime_command_receipt("pause-1")["actor"])
        second = self.service.execute(request)
        self.assertEqual(first, second)
        self.assertEqual(1, self.store.get_project_state("p1")["state_version"])
        conflict = self.service.execute(self.request("pause-1", "project.pause", payload={"x": 1}))
        self.assertEqual("REJECTED", conflict["status"])
        self.assertEqual("RUNTIME_RECEIPT_IDEMPOTENCY_CONFLICT", conflict["error"]["code"])

    def test_identical_request_replays_after_store_reopen(self):
        request = self.request("pause-reopen", "project.pause")
        first = self.service.execute(request)
        self.store.close()
        reopened = StateStore(self.root / "runtime.sqlite", [self.root])
        try:
            replay = OperatorControlService(reopened, daemon_epoch=1).execute(request)
            self.assertEqual(first, replay)
            self.assertEqual(1, reopened.get_project_state("p1")["state_version"])
        finally:
            reopened.close()

    def test_wrong_cas_and_daemon_epoch_are_rejected_without_transition(self):
        wrong_version = self.service.execute(self.request("bad-v", "project.pause", state_version=9))
        self.assertEqual("REJECTED", wrong_version["status"])
        self.assertEqual("STATE_VERSION_CONFLICT", wrong_version["error"]["code"])
        wrong_epoch = self.service.execute(self.request("bad-e", "project.pause", daemon_epoch=99))
        self.assertEqual("REJECTED", wrong_epoch["status"])
        self.assertEqual("DAEMON_EPOCH_CONFLICT", wrong_epoch["error"]["code"])
        self.assertEqual(0, self.store.get_project_state("p1")["state_version"])

    def test_resume_cancel_and_supersede_advance_generations(self):
        self.assertEqual("OK", self.service.execute(self.request("p", "project.pause"))["status"])
        self.assertEqual("OK", self.service.execute(self.request("r", "project.resume", state_version=1))["status"])
        self.assertEqual("OK", self.service.execute(self.request("c", "project.cancel", state_version=2))["status"])
        control = self.store.get_operator_control("p1")
        self.assertEqual("CANCELLED", control["operator_state"])
        self.assertEqual(3, control["operator_generation"])
        self.assertEqual(1, control["objective_generation"])
        supersede = self.service.execute(
            self.request("s", "project.supersede", state_version=3, payload={"objective_sha256": "b" * 64})
        )
        self.assertEqual("OK", supersede["status"])
        control = self.store.get_operator_control("p1")
        self.assertEqual("SUPERSEDED", control["operator_state"])
        self.assertEqual(4, control["operator_generation"])
        self.assertEqual(2, control["objective_generation"])
        self.assertEqual("b" * 64, control["objective_sha256"])
        resumed = self.service.execute(self.request("s-resume", "project.resume", state_version=4))
        self.assertEqual("OK", resumed["status"])
        self.assertEqual("RUNNING", self.store.get_operator_control("p1")["operator_state"])
        self.assertEqual(5, self.store.get_operator_control("p1")["operator_generation"])

    def test_supersede_requires_objective_hash(self):
        response = self.service.execute(self.request("missing", "project.supersede"))
        self.assertEqual("REJECTED", response["status"])
        self.assertEqual("OBJECTIVE_SHA256_REQUIRED", response["error"]["code"])
        self.assertEqual(0, self.store.get_project_state("p1")["state_version"])

    def test_resume_restores_scheduler_admission_after_pause(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler

        worktree = self.root / "resume-worktree"
        worktree.mkdir()
        scheduler = Scheduler(self.store, "p1", PathPolicy([worktree]), max_workers=2)
        scheduler.enqueue_graph([
            {
                "task_id": "resume-task",
                "objective_sha256": "e" * 64,
                "resource_scope": [worktree / "one.txt"],
                "dependencies": [],
            }
        ])

        self.assertEqual("OK", self.service.execute(self.request("pause-admission", "project.pause"))["status"])
        self.assertEqual(
            "OK",
            self.service.execute(self.request("resume-admission", "project.resume", state_version=1))["status"],
        )

        claims = scheduler.claim_runnable(master_epoch=0)
        self.assertEqual(1, len(claims))

    def test_resume_rehydrates_existing_claim_after_pause(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler

        worktree = self.root / "resume-existing-worktree"
        worktree.mkdir()
        scheduler = Scheduler(self.store, "p1", PathPolicy([worktree]), max_workers=2)
        scheduler.enqueue_graph([
            {
                "task_id": "existing-task",
                "objective_sha256": "f" * 64,
                "resource_scope": [worktree / "one.txt"],
                "dependencies": [],
            }
        ])
        claim = scheduler.claim_runnable(master_epoch=0)[0]

        self.assertEqual("OK", self.service.execute(self.request("pause-existing", "project.pause"))["status"])
        self.assertEqual(
            "OK",
            self.service.execute(self.request("resume-existing", "project.resume", state_version=1))["status"],
        )

        restored = scheduler.load_active_claims(master_epoch=0)
        self.assertEqual([claim.assignment_id], [item.assignment_id for item in restored])

    def test_cancel_fences_active_assignments_leases_and_pending_results(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler

        worktree = self.root / "worktree"
        worktree.mkdir()
        scheduler = Scheduler(self.store, "p1", PathPolicy([worktree]), max_workers=2)
        scheduler.enqueue_graph([
            {"task_id": "T1", "objective_sha256": "a" * 64, "resource_scope": [worktree / "one.txt"], "dependencies": []}
        ])
        claim = scheduler.claim_runnable(master_epoch=0, now=dt.datetime.now(dt.timezone.utc))[0]
        result_id = scheduler.record_candidate(
            claim.assignment_id,
            lease_token=claim.lease_token,
            master_epoch=0,
            kind="HANDOFF",
            payload={"artifact_sha256": "b" * 64, "result_sha256": "b" * 64},
        )
        response = self.service.execute(self.request("cancel-fence", "project.cancel"))
        self.assertEqual("OK", response["status"])
        with self.store._connection() as conn:
            self.assertEqual("FENCED", conn.execute("SELECT state FROM assignments WHERE assignment_id=?", (claim.assignment_id,)).fetchone()[0])
            self.assertEqual("FENCED", conn.execute("SELECT state FROM leases WHERE assignment_id=?", (claim.assignment_id,)).fetchone()[0])
            self.assertEqual("STALE", conn.execute("SELECT verification_state FROM candidate_results WHERE result_id=?", (result_id,)).fetchone()[0])

    def test_emergency_stop_is_durable_fenced_and_resume_requires_new_generation(self):
        response = self.service.execute(self.request("stop-1", "project.emergency_stop"))
        self.assertEqual("OK", response["status"])
        control = self.store.get_operator_control("p1")
        self.assertEqual("EMERGENCY_STOPPED", control["operator_state"])
        self.assertEqual(1, control["operator_generation"])
        self.assertEqual("EMERGENCY_STOPPED", self.store.get_project_state("p1")["status"])

        resumed = self.service.execute(self.request("resume-1", "project.resume", state_version=1))
        self.assertEqual("OK", resumed["status"])
        control = self.store.get_operator_control("p1")
        self.assertEqual("RUNNING", control["operator_state"])
        self.assertEqual(2, control["operator_generation"])

    def test_emergency_stop_is_idempotent_and_fences_new_work(self):
        first = self.service.execute(self.request("stop-2", "project.emergency_stop"))
        second = self.service.execute(self.request("stop-2", "project.emergency_stop"))
        self.assertEqual(first, second)
        self.assertEqual("EMERGENCY_STOPPED", self.store.get_operator_control("p1")["operator_state"])

        with self.store._connection() as conn:
            event = conn.execute(
                "SELECT kind FROM events WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                ("p1",),
            ).fetchone()
        self.assertEqual("EMERGENCY_STOPPED", event[0])

    def test_supersede_emits_objective_superseded_event(self):
        response = self.service.execute(
            self.request("supersede-event", "project.supersede", payload={"objective_sha256": "c" * 64})
        )
        self.assertEqual("OK", response["status"])
        with self.store._connection() as conn:
            event = conn.execute(
                "SELECT kind FROM events WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                ("p1",),
            ).fetchone()
        self.assertEqual("OBJECTIVE_SUPERSEDED", event[0])

    def test_superseded_operator_fence_rejects_new_graph_admission(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler, SchedulerError

        worktree = self.root / "superseded-worktree"
        worktree.mkdir()
        response = self.service.execute(
            self.request("supersede-graph", "project.supersede", payload={"objective_sha256": "d" * 64})
        )
        self.assertEqual("OK", response["status"])
        scheduler = Scheduler(self.store, "p1", PathPolicy([worktree]), max_workers=2)
        with self.assertRaisesRegex(SchedulerError, "OPERATOR_STATE_FENCED"):
            scheduler.enqueue_graph([
                {
                    "task_id": "T1",
                    "objective_sha256": "e" * 64,
                    "resource_scope": [worktree / "one.txt"],
                    "dependencies": [],
                }
            ])


if __name__ == "__main__":
    unittest.main()
