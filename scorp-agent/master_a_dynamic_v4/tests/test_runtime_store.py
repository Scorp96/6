from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.models import sha256_json
from master_a_dynamic_v4.state_store import StateStore


class RuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "runtime.sqlite"
        self.store = StateStore(self.db, [self.temp.name])
        self.contract = self.store.create_contract(
            "p1",
            root_contract={"objective": "demo"},
            acceptance_contract={"required": ["AC01"]},
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_contract_initializes_operator_generation_and_observation(self):
        control = self.store.get_operator_control("p1")
        self.assertEqual("RUNNING", control["operator_state"])
        self.assertEqual(0, control["operator_generation"])
        self.assertEqual(0, control["objective_generation"])
        self.assertEqual(
            sha256_json({"objective": "demo"}), control["objective_sha256"]
        )
        observation = self.store.get_runtime_observation("p1")
        self.assertEqual("IDLE", observation["progress_state"])
        self.assertIsNone(observation["last_progress_at"])

    def test_observation_upsert_is_bounded_and_idle_does_not_count_as_progress(self):
        self.store.record_runtime_observation(
            "p1",
            progress_state="IDLE",
            browser_semantic_state="READY",
            auth_host_blocker=None,
            observed_at="2026-09-15T00:00:00Z",
        )
        first = self.store.get_runtime_observation("p1")
        self.assertEqual("2026-09-15T00:00:00Z", first["last_observed_at"])
        self.assertIsNone(first["last_progress_at"])
        self.store.record_runtime_observation(
            "p1",
            progress_state="ACTIVE_GENERATING",
            browser_semantic_state="GENERATING",
            auth_host_blocker=None,
            observed_at="2026-09-15T00:00:02Z",
        )
        second = self.store.get_runtime_observation("p1")
        self.assertIsNone(second["last_progress_at"])
        self.assertEqual("2026-09-15T00:00:02Z", second["last_heartbeat_at"])
        self.store.record_runtime_observation(
            "p1",
            progress_state="ACTIVE_GENERATING",
            browser_semantic_state="GENERATING",
            auth_host_blocker=None,
            observed_at="2026-09-15T00:00:03Z",
            content_changed=True,
        )
        progressed = self.store.get_runtime_observation("p1")
        self.assertEqual("2026-09-15T00:00:03Z", progressed["last_progress_at"])
        self.assertEqual("2026-09-15T00:00:03Z", progressed["last_content_change_at"])
        self.store.record_runtime_observation(
            "p1",
            progress_state="IDLE",
            browser_semantic_state="READY",
            auth_host_blocker=None,
            observed_at="2026-09-15T00:00:04Z",
        )
        third = self.store.get_runtime_observation("p1")
        self.assertEqual("2026-09-15T00:00:03Z", third["last_progress_at"])
        self.assertEqual("2026-09-15T00:00:04Z", third["last_heartbeat_at"])

    def test_observation_has_heartbeat_column_after_v4_database_migration(self):
        with self.store._connection() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(runtime_observations)")}
        self.assertIn("last_heartbeat_at", columns)

    def test_runtime_command_receipt_round_trips_json_and_is_request_unique(self):
        response = {"status": "OK", "state_version": 1}
        saved = self.store.record_runtime_command_receipt(
            request_id="r1",
            receipt_id="receipt-r1",
            project_id="p1",
            command="project.pause",
            actor="operator",
            input_state_version=0,
            output_state_version=1,
            daemon_epoch=2,
            master_epoch=0,
            operator_generation=1,
            objective_generation=0,
            payload_sha256="a" * 64,
            status="OK",
            reason="PAUSED",
            response=response,
            created_at="2026-09-15T00:00:00Z",
        )
        self.assertEqual("receipt-r1", saved["receipt_id"])
        self.assertEqual(response, saved["response"])
        self.assertEqual("receipt-r1", self.store.get_runtime_command_receipt("r1")["receipt_id"])

    def test_reusing_existing_contract_repairs_missing_runtime_rows(self):
        with self.store._transaction() as conn:
            conn.execute("DELETE FROM operator_controls WHERE project_id='p1'")
            conn.execute("DELETE FROM runtime_observations WHERE project_id='p1'")
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"required": ["AC01"]}
        )
        self.assertEqual("RUNNING", self.store.get_operator_control("p1")["operator_state"])
        self.assertEqual("IDLE", self.store.get_runtime_observation("p1")["progress_state"])


if __name__ == "__main__":
    unittest.main()
