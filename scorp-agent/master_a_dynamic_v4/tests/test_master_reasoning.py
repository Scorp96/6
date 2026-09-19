from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.master_reasoning import MasterReasoningCoordinator
from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError


class _Controller:
    def __init__(self):
        self.plans = []

    def apply_plan(self, plan):
        self.plans.append(dict(plan))
        return {"project_id": plan["project_id"], "status": "ADMITTED"}


class _Adapter:
    def __init__(self, gateway):
        self.gateway = gateway
        self.reconcile_calls = 0

    def reconcile(self, intent_id):
        self.reconcile_calls += 1
        intent = self.gateway.store.get_intent(intent_id)
        return self.gateway._capture(intent_id, intent)


class _Gateway:
    def __init__(self, store, project_id="p"):
        self.store = store
        self.project_id = project_id
        self.submit_calls = 0
        self.block_first_submit = False
        self.mutate_after_capture = False
        self.next_action = "WAIT"
        self.adapter = _Adapter(self)

    def _decision(self, intent):
        payload = json.loads(intent["payload_json"])
        b = dict(payload["reasoning_binding"])
        value = {
            "master_decision_version": 1,
            **b,
            "action": self.next_action,
            "reason": "test decision",
        }
        if self.next_action == "APPLY_PLAN":
            value["plan"] = {
                "project_id": self.project_id,
                "master_identity": "A",
                "tasks": [
                    {
                        "task_id": "T1",
                        "objective_sha256": "a" * 64,
                        "resource_scope": [str(Path(self.store.allowed_roots[0]) / "a.txt")],
                        "dependencies": [],
                    }
                ],
            }
        return value

    def _capture(self, intent_id, intent):
        if intent["state"] in {"PREPARED", "VERIFIED_NOT_SUBMITTED"}:
            self.store.begin_possible_submit(intent_id)
        captured = self.store.capture_response(
            intent_id,
            response=self._decision(intent),
            conversation_url="https://chatgpt.com/c/master-test",
            remote_identity="master-test",
            observation={"source": "unit-test"},
        )
        self.store.finalize_intent(intent_id)
        return captured

    def submit_intent(self, intent_id):
        self.submit_calls += 1
        intent = self.store.get_intent(intent_id)
        self.store.begin_possible_submit(intent_id)
        if self.block_first_submit:
            return self.store.block_intent(
                intent_id,
                reason="SUBMIT_EXCEPTION_AMBIGUOUS",
                observation={"source": "unit-test"},
            )
        captured = self._capture(intent_id, self.store.get_intent(intent_id))
        if self.mutate_after_capture:
            with self.store._transaction() as conn:
                conn.execute(
                    "UPDATE project_state SET state_version=state_version+1 WHERE project_id=?",
                    (self.project_id,),
                )
        return captured


class MasterReasoningCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "state.sqlite3", [self.root])
        self.store.create_contract(
            "p",
            root_contract={"objective": "demo"},
            acceptance_contract={"required": []},
        )
        self.gateway = _Gateway(self.store)
        self.controller = _Controller()
        self.coordinator = MasterReasoningCoordinator(self.gateway, self.controller)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_snapshot_carries_human_objective_and_acceptance_contract(self):
        snapshot = self.coordinator.semantic_snapshot()
        self.assertEqual("demo", snapshot["contract"]["root"]["objective"])
        self.assertEqual([], snapshot["contract"]["acceptance"]["required"])

    def test_wait_is_exactly_once_and_quiesces_unchanged_state(self):
        self.assertTrue(self.coordinator.reasoning_required())
        first = self.coordinator.run_once()
        second = self.coordinator.run_once()
        self.assertEqual("IDLE", first["status"])
        self.assertEqual(first["intent_id"], second["intent_id"])
        self.assertEqual(1, self.gateway.submit_calls)
        self.assertFalse(self.coordinator.reasoning_required())
        with self.store._connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM action_intents WHERE project_id='p' AND action_kind='MASTER_REASONING'"
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_ambiguous_submit_reconciles_same_intent_without_resend(self):
        self.gateway.block_first_submit = True
        first = self.coordinator.run_once()
        self.assertEqual("WAITING", first["status"])
        self.assertEqual(1, self.gateway.submit_calls)
        self.gateway.block_first_submit = False
        second = self.coordinator.run_once()
        self.assertEqual("IDLE", second["status"])
        self.assertEqual(first["intent_id"], second["intent_id"])
        self.assertEqual(1, self.gateway.submit_calls)
        self.assertEqual(1, self.gateway.adapter.reconcile_calls)
        self.assertEqual(1, self.store.get_intent(first["intent_id"])["attempt"])

    def test_state_change_after_capture_fences_stale_decision(self):
        self.gateway.mutate_after_capture = True
        result = self.coordinator.run_once()
        self.assertEqual("STALE", result["status"])
        self.assertEqual([], self.controller.plans)
        self.assertTrue(self.coordinator.reasoning_required())

    def test_apply_plan_calls_existing_controller_once(self):
        self.gateway.next_action = "APPLY_PLAN"
        result = self.coordinator.run_once()
        self.assertEqual("APPLIED", result["status"])
        self.assertEqual(1, len(self.controller.plans))
        self.assertEqual("p", self.controller.plans[0]["project_id"])

    def test_pre_io_authority_fences_stale_reasoning_intent(self):
        intent = self.coordinator._prepare()
        with self.store._transaction() as conn:
            conn.execute(
                "UPDATE project_state SET state_version=state_version+1 WHERE project_id='p'"
            )
        with self.assertRaises(StoreInvariantError):
            self.store.assert_intent_generation(intent["intent_id"])

    def test_known_not_submitted_authority_fence_retires_without_reconcile(self):
        intent = self.coordinator._prepare()
        with self.store._transaction() as conn:
            conn.execute(
                "UPDATE project_state SET state_version=state_version+1 WHERE project_id='p'"
            )
        blocked = self.store.block_intent(
            intent["intent_id"],
            reason="OPERATOR_GENERATION_FENCED:MASTER_REASONING_AUTHORITY_FENCED",
            observation={
                "side_effect": "NOT_ATTEMPTED",
                "reason": "MASTER_REASONING_AUTHORITY_FENCED",
            },
        )
        self.assertEqual("BLOCKED_AMBIGUOUS", blocked["state"])
        result = self.coordinator.run_once()
        self.assertEqual("STALE", result["status"])
        self.assertEqual(0, self.gateway.adapter.reconcile_calls)
        self.assertEqual(
            "FENCED_AMBIGUOUS",
            self.store.get_intent(intent["intent_id"])["state"],
        )
        self.assertTrue(self.coordinator.reasoning_required())


if __name__ == "__main__":
    unittest.main()
