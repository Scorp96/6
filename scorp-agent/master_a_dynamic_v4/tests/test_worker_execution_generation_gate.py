"""Offline negative tests for Master-issued Worker LOCAL_EXECUTION capabilities.

All tests use a temporary SQLite database. No browser, privileged broker,
Windows task, external network request, or real subprocess is invoked.
"""

import pathlib
import tempfile
import unittest

from master_a_dynamic_v4.path_policy import PathPolicy
from master_a_dynamic_v4.scheduler import Scheduler
from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError


class WorkerExecutionGenerationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.project = "isolated-worker-generation-contract"
        self.store = StateStore(self.root / "state.sqlite3", [self.root])
        self.addCleanup(self.store.close)
        self.store.create_contract(
            self.project,
            root_contract={"objective": "offline Worker generation authority"},
            acceptance_contract={"ids": []},
        )
        self.scheduler = Scheduler(
            self.store, self.project, PathPolicy([self.root]), max_workers=2
        )
        self.scheduler.enqueue_graph([
            {
                "task_id": "T1",
                "objective_sha256": "a" * 64,
                "resource_scope": [str(self.root)],
                "access_mode": "read",
                "dependencies": [],
            }
        ])
        self.claim = self.scheduler.claim_runnable(master_epoch=0, limit=1)[0]
        self.counter = 0

    def _prepare(self, *, worker_overrides=None, payload_overrides=None, actor=None):
        self.counter += 1
        c = self.claim
        control = self.store.get_operator_control(self.project)
        binding = {
            "assignment_id": c.assignment_id,
            "task_id": c.task_id,
            "worker_id": c.worker_id,
            "slot_id": c.slot_id,
            "master_epoch": c.master_epoch,
            "base_state_version": c.base_state_version,
            "lease_token": c.lease_token,
            "operator_generation": c.operator_generation,
            "objective_generation": c.objective_generation,
            "resource_scope": list(c.resource_scope),
            "access_mode": c.access_mode,
        }
        binding.update(worker_overrides or {})
        payload = {
            "assignment_id": c.assignment_id,
            "task_id": c.task_id,
            "master_epoch": c.master_epoch,
            "lease_token": c.lease_token,
            "operator_generation": int(control["operator_generation"]),
            "objective_generation": int(control["objective_generation"]),
            "worker_assignment": binding,
            "request": {"module": "no-execution-in-this-test"},
        }
        payload.update(payload_overrides or {})
        intent_id = f"offline-local-worker-{self.counter}"
        self.store.prepare_intent(
            self.project, intent_id,
            actor_id=c.worker_id if actor is None else actor,
            channel=f"execution/{c.slot_id}",
            action_kind="LOCAL_EXECUTION",
            payload=payload,
        )
        return intent_id

    def test_issued_active_lease_and_generations_are_admitted(self):
        self.store.assert_intent_generation(self._prepare())

    def test_mismatched_original_operator_generation_is_fenced(self):
        intent_id = self._prepare(worker_overrides={"operator_generation": 77})
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_EXECUTION_GENERATION_FENCED"):
            self.store.assert_intent_generation(intent_id)

    def test_stale_issued_claim_cannot_use_fresh_root_objective_generation(self):
        # Simulate an operator/objective revision without relying on the separate
        # revocation path: the Worker claim must independently fence old epochs.
        with self.store._transaction() as conn:
            conn.execute(
                "UPDATE operator_controls SET objective_generation=objective_generation+1 WHERE project_id=?",
                (self.project,),
            )
        intent_id = self._prepare()
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_EXECUTION_GENERATION_FENCED"):
            self.store.assert_intent_generation(intent_id)

    def test_stale_issued_claim_cannot_use_fresh_root_operator_generation(self):
        with self.store._transaction() as conn:
            conn.execute(
                "UPDATE operator_controls SET operator_generation=operator_generation+1 WHERE project_id=?",
                (self.project,),
            )
        intent_id = self._prepare()
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_EXECUTION_GENERATION_FENCED"):
            self.store.assert_intent_generation(intent_id)

    def test_worker_capability_missing_generations_is_fenced(self):
        claim = self.claim
        control = self.store.get_operator_control(self.project)
        intent_id = self._prepare()
        # Keep the old-style intent immutable and separately persisted.
        self.counter += 1
        legacy_id = f"offline-local-worker-{self.counter}"
        missing = {
            "assignment_id": claim.assignment_id,
            "task_id": claim.task_id,
            "worker_id": claim.worker_id,
            "slot_id": claim.slot_id,
            "master_epoch": claim.master_epoch,
            "base_state_version": claim.base_state_version,
            "lease_token": claim.lease_token,
            "resource_scope": list(claim.resource_scope),
            "access_mode": claim.access_mode,
        }
        self.store.prepare_intent(
            self.project, legacy_id,
            actor_id=claim.worker_id,
            channel=f"execution/{claim.slot_id}",
            action_kind="LOCAL_EXECUTION",
            payload={
                "operator_generation": int(control["operator_generation"]),
                "objective_generation": int(control["objective_generation"]),
                "worker_assignment": missing,
            },
        )
        self.store.assert_intent_generation(intent_id)
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_EXECUTION_GENERATION_BINDING_MISSING"):
            self.store.assert_intent_generation(legacy_id)

    def test_worker_boolean_generation_is_not_an_integer_capability(self):
        intent_id = self._prepare(worker_overrides={"operator_generation": False})
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_EXECUTION_GENERATION_BINDING_INVALID"):
            self.store.assert_intent_generation(intent_id)

    def test_cross_worker_lease_token_cannot_be_reused(self):
        intent_id = self._prepare(worker_overrides={"lease_token": "other-worker-lease"})
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_AUTHORITY_FENCED"):
            self.store.assert_intent_generation(intent_id)

    def test_expired_lease_is_fenced(self):
        intent_id = self._prepare()
        with self.store._transaction() as conn:
            conn.execute(
                "UPDATE leases SET expires_at='2000-01-01T00:00:00Z' WHERE assignment_id=?",
                (self.claim.assignment_id,),
            )
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_LEASE_EXPIRED"):
            self.store.assert_intent_generation(intent_id)

    def test_different_actor_cannot_use_worker_capability(self):
        intent_id = self._prepare(actor="worker-impostor")
        with self.assertRaisesRegex(StoreInvariantError, "WORKER_AUTHORITY_FENCED"):
            self.store.assert_intent_generation(intent_id)

    def test_root_generation_cannot_self_authorize(self):
        intent_id = self._prepare(payload_overrides={"operator_generation": 999})
        with self.assertRaisesRegex(StoreInvariantError, "OPERATOR_GENERATION_FENCED"):
            self.store.assert_intent_generation(intent_id)


if __name__ == "__main__":
    unittest.main()
