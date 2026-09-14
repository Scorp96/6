from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.operator_control import OperatorControlService
from master_a_dynamic_v4.runtime_commands import RuntimeCommandService
from master_a_dynamic_v4.runtime_protocol import error_response, protocol_error
from master_a_dynamic_v4.state_store import StateStore


class RuntimePublicApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"ids": []}
        )
        self.lease = self.store.acquire_daemon_lease("p1", "daemon")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_protocol_error_is_a_machine_readable_alias(self):
        self.assertEqual(
            error_response("r", status="REJECTED", code="BAD"),
            protocol_error("r", code="BAD"),
        )

    def test_store_exposes_planned_snapshot_gate_and_receipt_names(self):
        snapshot = self.store.runtime_snapshot("p1", self.lease["daemon_epoch"])
        self.assertEqual("p1", snapshot["project_id"])
        self.assertEqual("RUNNING", snapshot["operator"]["operator_state"])
        gate = self.store.external_side_effect_gate("p1", 0, 0)
        self.assertEqual("ALLOWED", gate["status"])
        self.assertIsNone(self.store.get_command_receipt("missing"))

    def test_service_and_operator_accept_planned_constructor_and_method_aliases(self):
        service = RuntimeCommandService(
            self.store, project_id="p1", actor_id="sol-runtime"
        )
        self.assertEqual("p1", service.project_id)
        operator = OperatorControlService(
            self.store, daemon_epoch=self.lease["daemon_epoch"]
        )
        self.assertTrue(callable(operator.apply))


if __name__ == "__main__":
    unittest.main()
