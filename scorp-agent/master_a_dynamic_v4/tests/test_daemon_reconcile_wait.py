from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.activation_arbiter import ArbiterSnapshot
from master_a_dynamic_v4.daemon import LocalDaemon
from master_a_dynamic_v4.persistent_controller_handler import PersistentControllerActionHandler
from master_a_dynamic_v4.state_store import StateStore


class _Controller:
    def __init__(self):
        self.step_calls = 0
    def step(self, _prompt_factory):
        self.step_calls += 1
        raise AssertionError("controller.step must not run while browser intent is ambiguous")


class PersistentReconcileWaitTests(unittest.TestCase):
    def test_blocked_ambiguous_reconcile_remains_observation_retryable_without_resend(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p1", root_contract={"objective": "wait"}, acceptance_contract={"required": []})
            controller = _Controller()
            recover_calls = []
            def recover():
                recover_calls.append(True)
                return [("intent-1", "BLOCKED_AMBIGUOUS")]
            handler = PersistentControllerActionHandler(
                controller, worker_prompt_factory=lambda _claim: "unused", recover_callback=recover)
            snapshot = ArbiterSnapshot(
                project_id="p1", project_status="ACTIVE", master_epoch=0, daemon_epoch=1,
                master_active=True, active_workers=1, free_slots=1, ready_tasks=0, ambiguous_intents=1)
            daemon = LocalDaemon(
                store, project_id="p1", daemon_epoch=1, snapshot_provider=lambda: snapshot,
                action_handlers={"RECONCILE_AMBIGUOUS": handler}, health_path=root / "health.json")
            try:
                decisions = daemon.run_loop(interval_seconds=0, max_iterations=2, sleep=lambda _x: None)
            finally:
                store.close()
            self.assertEqual(2, len(decisions), "persistent daemon must keep observing an already-submitted ambiguous intent")
            self.assertEqual([True, True], recover_calls)
            self.assertEqual(0, controller.step_calls)
            self.assertTrue(all(d.action == "RECONCILE_AMBIGUOUS" for d in decisions))


if __name__ == "__main__":
    unittest.main()