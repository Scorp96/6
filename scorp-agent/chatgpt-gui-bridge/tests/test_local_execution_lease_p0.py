"""P0: no stale V4 Worker lease may reserve or dispatch new local work.

No browser sends, no process spawning, no actual file mutation outside temp SQLite.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile
import unittest

BRIDGE = pathlib.Path(__file__).resolve().parents[1]
AGENT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BRIDGE))
sys.path.insert(0, str(AGENT))

from v4_bridge_gateway import V4BridgeGateway
from master_a_dynamic_v4.state_store import StoreInvariantError


class NoBrowserEngine:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("REAL_BROWSER_SEND_FORBIDDEN")
    def auth_state(self, channel):
        return {"status": "AUTHENTICATED", "channel": channel}


class LocalExecutionLiveLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        workspace = root / "work"
        workspace.mkdir()
        self.gateway = V4BridgeGateway(
            root / "state.sqlite3", "P0-live-lease", [workspace], NoBrowserEngine()
        )
        self.addCleanup(self.gateway.close)
        self.gateway.ensure_contract(
            {"objective": "prevent stale local execution"},
            {"required": ["AC-LEASE"]},
        )
        self.gateway.enqueue_graph([{
            "task_id": "T1",
            "objective_sha256": "1" * 64,
            "resource_scope": [workspace / "proof.txt"],
            "dependencies": [],
            "access_mode": "read",
        }])
        self.claim = self.gateway.claim_workers(master_epoch=0, limit=1)[0]
        self.store = self.gateway.store
        self.intent_id = "execution-intent-" + self.claim.assignment_id
        self.store.prepare_intent(
            self.claim.project_id, self.intent_id,
            actor_id=self.claim.worker_id,
            channel="execution/" + self.claim.slot_id,
            action_kind="LOCAL_EXECUTION",
            payload={
                "assignment_id": self.claim.assignment_id,
                "task_id": self.claim.task_id,
                "master_epoch": self.claim.master_epoch,
                "operator_generation": self.claim.operator_generation,
                "objective_generation": self.claim.objective_generation,
                "lease_token": self.claim.lease_token,
                "request": {"access_mode": self.claim.access_mode, "module": "no-op"},
            },
        )

    def change(self, sql, *args):
        with self.store._transaction() as conn:
            conn.execute(sql, args)

    def assert_denied_without_side_effect_reservation(self):
        with self.assertRaisesRegex(
            StoreInvariantError, "LOCAL_EXECUTION_AUTHORITY_FENCED"
        ):
            self.store.begin_possible_submit(self.intent_id)
        row = self.store.get_intent(self.intent_id)
        self.assertEqual("PREPARED", row["state"])
        self.assertEqual("PENDING", self.store.get_outbox_for_intent(self.intent_id)["state"])

    def test_valid_active_lease_can_reserve_once(self):
        self.store.assert_local_execution_lease(self.intent_id)
        row = self.store.begin_possible_submit(self.intent_id)
        self.assertEqual("MAY_HAVE_SUBMITTED", row["state"])
        self.store.assert_local_execution_lease(self.intent_id)

    def test_revoked_lease_blocks_prepared_to_possible_submit(self):
        self.change(
            "UPDATE leases SET state='EXPIRED' WHERE assignment_id=?",
            self.claim.assignment_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_master_takeover_fences_old_lease_before_reserving(self):
        self.change(
            "UPDATE project_state SET master_epoch=master_epoch+1 WHERE project_id=?",
            self.claim.project_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_operator_generation_change_fences_old_work_before_reservation(self):
        self.change(
            "UPDATE project_state SET operator_generation=operator_generation+1 WHERE project_id=?",
            self.claim.project_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_objective_generation_change_fences_old_work_before_reservation(self):
        self.change(
            "UPDATE project_state SET objective_generation=objective_generation+1 WHERE project_id=?",
            self.claim.project_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_issued_generations_are_durably_bound_to_active_assignment(self):
        state = self.store.get_project_state(self.claim.project_id)
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT operator_generation,objective_generation FROM assignments WHERE assignment_id=?",
                (self.claim.assignment_id,),
            ).fetchone()
        self.assertEqual(int(row["operator_generation"]), self.claim.operator_generation)
        self.assertEqual(int(row["objective_generation"]), self.claim.objective_generation)
        self.assertEqual(int(state["operator_generation"]), self.claim.operator_generation)
        self.assertEqual(int(state["objective_generation"]), self.claim.objective_generation)

    def test_graph_version_change_fences_old_local_work(self):
        self.change(
            "UPDATE project_state SET state_version=state_version+1 WHERE project_id=?",
            self.claim.project_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_expired_lease_blocks_before_reservation(self):
        self.change(
            "UPDATE leases SET expires_at=? WHERE assignment_id=?",
            "2020-01-01T00:00:00Z",
            self.claim.assignment_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_disabled_project_blocks_before_reservation(self):
        self.change(
            "UPDATE project_state SET status='PAUSED' WHERE project_id=?",
            self.claim.project_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_lost_task_state_blocks_before_reservation(self):
        self.change(
            "UPDATE task_nodes SET state='QUEUED' WHERE project_id=? AND task_id=?",
            self.claim.project_id, self.claim.task_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_wrong_worker_identity_blocks_before_reservation(self):
        self.change(
            "UPDATE action_intents SET actor_id='other-worker' WHERE intent_id=?",
            self.intent_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_late_revocation_after_maybe_submit_is_detected_before_executor(self):
        self.store.begin_possible_submit(self.intent_id)
        self.change(
            "UPDATE leases SET state='EXPIRED' WHERE assignment_id=?",
            self.claim.assignment_id,
        )
        with self.assertRaisesRegex(
            StoreInvariantError, "LOCAL_EXECUTION_AUTHORITY_FENCED"
        ):
            self.store.assert_local_execution_lease(self.intent_id)
        self.assertEqual(
            "MAY_HAVE_SUBMITTED", self.store.get_intent(self.intent_id)["state"]
        )

    def test_invalid_token_is_not_accepted_before_reservation(self):
        self.change(
            "UPDATE action_intents SET payload_json=? WHERE intent_id=?",
            '{"assignment_id":"%s","task_id":"T1","master_epoch":0,"lease_token":"forged","request":{"access_mode":"read"}}'
            % self.claim.assignment_id,
            self.intent_id,
        )
        self.assert_denied_without_side_effect_reservation()

    def test_non_execution_intents_keep_existing_browser_contract(self):
        browser_id = "other-readonly-intent"
        self.store.prepare_intent(
            self.claim.project_id, browser_id,
            actor_id="master", channel="master",
            action_kind="CHATGPT_WORKER_SUBMIT", payload={"prompt": "fixture"},
        )
        row = self.store.begin_possible_submit(browser_id)
        self.assertEqual("MAY_HAVE_SUBMITTED", row["state"])


if __name__ == "__main__":
    unittest.main()
