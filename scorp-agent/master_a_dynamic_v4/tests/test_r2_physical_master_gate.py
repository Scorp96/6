from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.activation_arbiter import ActivationArbiter, ArbiterSnapshot
from master_a_dynamic_v4.browser_adapter import BrowserAdapter
from master_a_dynamic_v4.state_store import StateStore


class _NoIoEngine:
    def __init__(self) -> None:
        self.submit_count = 0

    def auth_state(self, _channel: str):
        return {"status": "AUTHENTICATED"}

    def submit(self, _intent):
        self.submit_count += 1
        raise AssertionError("BROWSER_IO_MUST_NOT_RUN")

    def reconcile(self, _intent):
        raise AssertionError("RECONCILE_MUST_NOT_RUN")


class R2PhysicalMasterGateTests(unittest.TestCase):
    def setUp(self):
        self.arbiter = ActivationArbiter(actor_id="daemon-r2-test")

    def _snapshot(self, **overrides):
        values = {
            "project_id": "p",
            "project_status": "ACTIVE",
            "master_epoch": 4,
            "daemon_epoch": 8,
            "master_active": True,
            "active_workers": 0,
            "free_slots": 2,
            "ready_tasks": 2,
            "ambiguous_intents": 0,
            "master_physical_required": True,
            "master_physical_bound": False,
            "master_physical_verified": False,
        }
        values.update(overrides)
        return ArbiterSnapshot(**values)

    def test_strict_master_without_binding_bootstraps_before_worker_assignment(self):
        decision = self.arbiter.decide(self._snapshot())
        self.assertEqual("REASON_MASTER", decision.action)
        self.assertEqual("MASTER_PHYSICAL_BOOTSTRAP_REQUIRED", decision.reason)
        self.assertEqual(0, decision.capacity)

    def test_strict_bound_master_is_verified_before_worker_assignment(self):
        decision = self.arbiter.decide(
            self._snapshot(
                master_physical_bound=True,
                master_physical_verified=False,
            )
        )
        self.assertEqual("VERIFY_MASTER", decision.action)
        self.assertEqual("MASTER_PHYSICAL_VERIFICATION_REQUIRED", decision.reason)
        self.assertEqual(0, decision.capacity)

    def test_strict_verified_master_can_assign_worker(self):
        decision = self.arbiter.decide(
            self._snapshot(
                master_physical_bound=True,
                master_physical_verified=True,
            )
        )
        self.assertEqual("ASSIGN_WORKER", decision.action)
        self.assertEqual(2, decision.capacity)

    def test_non_strict_projects_keep_existing_assignment_behavior(self):
        decision = self.arbiter.decide(
            self._snapshot(
                master_physical_required=False,
                master_physical_bound=False,
                master_physical_verified=False,
            )
        )
        self.assertEqual("ASSIGN_WORKER", decision.action)
        self.assertEqual(2, decision.capacity)

    def test_same_url_physical_verification_refreshes_evidence_without_generation_bump(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            try:
                store.create_contract(
                    "p",
                    root_contract={"objective": "r2"},
                    acceptance_contract={"required": []},
                )
                url = "https://chatgpt.com/c/master-r2"
                first = store.rebind_browser(
                    "p",
                    "master",
                    actor_id="A",
                    conversation_url=url,
                    predecessor_url=None,
                    reason="INITIAL_BINDING",
                    evidence={"source": "initial", "daemon_epoch": 1},
                )
                second = store.rebind_browser(
                    "p",
                    "master",
                    actor_id="A",
                    conversation_url=url,
                    predecessor_url=url,
                    reason="PHYSICAL_SESSION_VERIFIED",
                    evidence={
                        "source": "verify",
                        "daemon_epoch": 2,
                        "master_epoch": 4,
                        "session": "scorp-p0-conv-r2",
                    },
                )
                persisted = store.get_browser_binding("p", "master")
                self.assertEqual(0, int(first["generation"]))
                self.assertEqual(0, int(second["generation"]))
                self.assertEqual(0, int(persisted["generation"]))
                self.assertEqual("PHYSICAL_SESSION_VERIFIED", persisted["rebind_reason"])
                evidence = json.loads(persisted["evidence_json"])
                self.assertEqual("verify", evidence["source"])
                self.assertEqual(2, evidence["daemon_epoch"])
                self.assertEqual("scorp-p0-conv-r2", evidence["session"])
            finally:
                store.close()

    def test_rotated_master_transport_generation_fences_old_intent_before_browser_io(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            try:
                store.create_contract(
                    "p",
                    root_contract={"objective": "r2"},
                    acceptance_contract={"required": []},
                )
                first_url = "https://chatgpt.com/c/master-r2-a"
                second_url = "https://chatgpt.com/c/master-r2-b"
                first = store.rebind_browser(
                    "p",
                    "master",
                    actor_id="A",
                    conversation_url=first_url,
                    predecessor_url=None,
                    reason="INITIAL_BINDING",
                    evidence={"source": "initial"},
                )
                state = store.get_project_state("p")
                control = store.get_operator_control("p")
                intent_id = "master-reasoning-r2-transport"
                reasoning_binding = {
                    "project_id": "p",
                    "intent_id": intent_id,
                    "master_epoch": int(state["master_epoch"]),
                    "base_state_version": int(state["state_version"]),
                    "operator_generation": int(control["operator_generation"]),
                    "objective_generation": int(control["objective_generation"]),
                    "input_snapshot_sha256": "a" * 64,
                }
                store.prepare_intent(
                    "p",
                    intent_id,
                    actor_id="A",
                    channel="master",
                    action_kind="MASTER_REASONING",
                    payload={
                        "prompt": "test",
                        "reasoning_binding": reasoning_binding,
                        "transport_binding": {
                            "channel": "master",
                            "actor_id": "A",
                            "conversation_url": first_url,
                            "generation": int(first["generation"]),
                        },
                        "operator_generation": int(control["operator_generation"]),
                        "objective_generation": int(control["objective_generation"]),
                    },
                )
                rotated = store.rebind_browser(
                    "p",
                    "master",
                    actor_id="A",
                    conversation_url=second_url,
                    predecessor_url=first_url,
                    reason="EXPLICIT_ROTATION",
                    evidence={"source": "rotation"},
                )
                self.assertEqual(1, int(rotated["generation"]))

                engine = _NoIoEngine()
                adapter = BrowserAdapter(store, engine)
                result = adapter.submit_once(intent_id)

                self.assertEqual(0, engine.submit_count)
                self.assertEqual("BLOCKED_AMBIGUOUS", result["state"])
                self.assertIn(
                    "MASTER_REASONING_TRANSPORT_BINDING_FENCED",
                    str(result["ambiguity_reason"]),
                )
                observation = json.loads(str(result["observation_json"]))
                self.assertEqual("NOT_ATTEMPTED", observation["side_effect"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
