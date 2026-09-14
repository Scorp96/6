from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.runtime_commands import RuntimeCommandService
from master_a_dynamic_v4.runtime_protocol import parse_request
from master_a_dynamic_v4.state_store import StateStore


class RuntimeCommandContractV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"required": []}
        )
        self.lease = self.store.acquire_daemon_lease("p1", "daemon")
        self.service = RuntimeCommandService(self.store, daemon_epoch=self.lease["daemon_epoch"])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def request(self, request_id: str, command: str, *, actor: str = "gpt-master", **extra):
        value = {
            "protocol_version": "scorp.runtime.command/1",
            "request_id": request_id,
            "command": command,
            "project_id": "p1",
            "actor": actor,
            "payload": {},
        }
        value.update(extra)
        return parse_request(value)

    def test_snapshot_and_worker_status_are_registered_and_bounded(self):
        snapshot = self.service.execute(self.request("snapshot", "runtime.snapshot"))
        self.assertEqual("OK", snapshot["status"])
        self.assertEqual("p1", snapshot["result"]["project_id"])
        self.assertEqual(0, snapshot["result"]["workers"]["active"])

        workers = self.service.execute(self.request("workers", "worker.status"))
        self.assertEqual("OK", workers["status"])
        self.assertEqual("p1", workers["result"]["project_id"])
        self.assertLessEqual(len(workers["result"]["items"]), 2)

    def test_new_contract_uses_explicit_running_operator_state(self):
        self.assertEqual("RUNNING", self.store.get_operator_control("p1")["operator_state"])

    def test_actor_and_epoch_generation_are_in_response_and_stale_master_is_fenced(self):
        status = self.service.execute(self.request("status", "runtime.status"))
        self.assertEqual("gpt-master", status["result"]["actor"])
        self.assertIn("master_epoch", status)
        self.assertIn("generation", status)

        state = self.store.get_project_state("p1")
        fenced = self.service.execute(self.request(
            "pause-stale-master",
            "project.pause",
            expected_state_version=state["state_version"],
            expected_daemon_epoch=self.lease["daemon_epoch"],
            expected_master_epoch=99,
        ))
        self.assertEqual("REJECTED", fenced["status"])
        self.assertEqual("MASTER_EPOCH_CONFLICT", fenced["error"]["code"])


if __name__ == "__main__":
    unittest.main()
