from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.runtime_commands import RuntimeCommandService
from master_a_dynamic_v4.runtime_protocol import parse_request
from master_a_dynamic_v4.state_store import StateStore


class RuntimeReadCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"required": []}
        )
        self.lease = self.store.acquire_daemon_lease("p1", "daemon-1")
        self.service = RuntimeCommandService(self.store, daemon_epoch=self.lease["daemon_epoch"])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def request(self, command, payload=None):
        return parse_request(
            {
                "protocol_version": "scorp.runtime.command/1",
                "request_id": "read-" + command,
                "command": command,
                "project_id": "p1",
                "payload": payload or {},
            }
        )

    def test_runtime_and_project_status_are_bounded_snapshots(self):
        response = self.service.execute(self.request("runtime.status"))
        self.assertEqual("OK", response["status"])
        result = response["result"]
        self.assertEqual("p1", result["project_id"])
        self.assertEqual("ACTIVE", result["operator"]["operator_state"])
        self.assertEqual("IDLE", result["observation"]["progress_state"])
        self.assertEqual(0, result["workers"]["active"])
        project = self.service.execute(self.request("project.status"))
        self.assertEqual("ACTIVE", project["result"]["project"]["status"])
        self.assertNotIn("events", project["result"])

    def test_master_status_and_empty_evidence_query(self):
        master = self.service.execute(self.request("master.status"))
        self.assertEqual("OK", master["status"])
        self.assertIsNone(master["result"]["master"])
        evidence = self.service.execute(self.request("evidence.query", {"limit": 100}))
        self.assertEqual("OK", evidence["status"])
        self.assertEqual([], evidence["result"]["items"])
        self.assertEqual(100, evidence["result"]["limit"])

    def test_evidence_query_rejects_unbounded_limit(self):
        response = self.service.execute(self.request("evidence.query", {"limit": 101}))
        self.assertEqual("REJECTED", response["status"])
        self.assertEqual("EVIDENCE_LIMIT_INVALID", response["error"]["code"])

    def test_evidence_query_applies_receipt_and_time_filters(self):
        self.store.record_runtime_command_receipt(
            request_id="evidence-r1", receipt_id="receipt-e1", project_id="p1",
            command="project.pause", actor="operator", input_state_version=0,
            output_state_version=1, daemon_epoch=1, master_epoch=0,
            operator_generation=1, objective_generation=0, payload_sha256="a" * 64,
            status="OK", reason="PAUSED", response={"status": "OK"},
            created_at="2026-09-15T00:00:00Z",
        )
        selected = self.service.execute(
            self.request("evidence.query", {
                "receipt_id": "receipt-e1", "since": "2026-09-14T00:00:00Z",
                "until": "2026-09-16T00:00:00Z", "limit": 10,
            })
        )
        self.assertEqual("OK", selected["status"])
        self.assertEqual(["receipt-e1"], [item["receipt_id"] for item in selected["result"]["items"]])


if __name__ == "__main__":
    unittest.main()
