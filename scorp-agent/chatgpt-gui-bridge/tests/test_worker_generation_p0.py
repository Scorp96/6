"""P0 mainline generation-fencing regression; SQLite temporary fixtures only."""
from __future__ import annotations

import contextlib
import dataclasses
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

BASE = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from master_a_dynamic_v4.scheduler import WorkerFenceError
from master_a_dynamic_v4.state_store import StateStore
from v4_bridge_gateway import V4BridgeGateway


class NoNetworkEngine:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("REAL_CHATGPT_SEND_FORBIDDEN")


class WorkerGenerationP0Tests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = pathlib.Path(td.name)
        self.work = root / "work"
        self.work.mkdir()
        self.gateway = V4BridgeGateway(root / "state.sqlite3", "generation-project", [self.work], NoNetworkEngine())
        self.addCleanup(self.gateway.close)
        self.gateway.ensure_contract({"objective": "generation gate"}, {"required": ["AC-GENERATION"]})
        self.gateway.enqueue_graph([{
            "task_id": "T1", "objective_sha256": "1" * 64,
            "resource_scope": [self.work / "one.txt"], "dependencies": [], "access_mode": "read",
        }])
        self.claim = self.gateway.claim_workers(master_epoch=0, limit=1)[0]

    def bump(self, field):
        self.assertIn(field, {"operator_generation", "objective_generation"})
        with self.gateway.store._transaction() as conn:
            conn.execute(
                f"UPDATE project_state SET {field}={field}+1 WHERE project_id=?",
                (self.claim.project_id,),
            )

    def test_fresh_claim_binds_both_initial_generations(self):
        self.assertEqual(0, self.claim.operator_generation)
        self.assertEqual(0, self.claim.objective_generation)
        self.gateway.store.get_project_state(self.claim.project_id)
        stored = self.gateway.load_worker_claims(master_epoch=0)
        self.assertEqual(1, len(stored))
        self.assertEqual(self.claim.operator_generation, stored[0].operator_generation)
        self.assertEqual(self.claim.objective_generation, stored[0].objective_generation)

    def test_browser_payload_contains_nonsecret_issued_generations(self):
        intent = self.gateway.prepare_worker_intent(self.claim, "Perform T1 safely")
        assignment = json.loads(intent["payload_json"])["worker_assignment"]
        self.assertEqual(0, assignment["operator_generation"])
        self.assertEqual(0, assignment["objective_generation"])
        self.assertNotIn(self.claim.lease_token, intent["payload_json"])

    def test_revoked_operator_generation_rejects_new_worker_browser_intent(self):
        self.bump("operator_generation")
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(self.claim, "do T1")
        with self.gateway.store._connection() as conn:
            n = conn.execute(
                "SELECT count(*) FROM action_intents WHERE project_id=?",
                (self.claim.project_id,),
            ).fetchone()[0]
        self.assertEqual(0, n)

    def test_revoked_objective_generation_rejects_new_worker_browser_intent(self):
        self.bump("objective_generation")
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(self.claim, "do T1")

    def test_revoked_operator_generation_rejects_candidate_result(self):
        self.bump("operator_generation")
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.scheduler.record_candidate(
                self.claim.assignment_id,
                lease_token=self.claim.lease_token,
                master_epoch=self.claim.master_epoch,
                kind="HANDOFF", payload={"handoff": "fixture"},
            )
        with self.gateway.store._connection() as conn:
            n = conn.execute(
                "SELECT count(*) FROM candidate_results WHERE assignment_id=?",
                (self.claim.assignment_id,),
            ).fetchone()[0]
        self.assertEqual(0, n)

    def test_new_authority_generation_cannot_retroactively_upgrade_old_claim(self):
        self.bump("operator_generation")
        self.bump("objective_generation")
        loaded = self.gateway.load_worker_claims(master_epoch=0)
        self.assertEqual(1, len(loaded))
        self.assertEqual(0, loaded[0].operator_generation)
        self.assertEqual(0, loaded[0].objective_generation)
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(loaded[0], "stale old claim")

    def test_paused_project_blocks_new_worker_intent_even_without_generation_change(self):
        with self.gateway.store._transaction() as conn:
            conn.execute("UPDATE project_state SET status='PAUSED' WHERE project_id=?", (self.claim.project_id,))
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(self.claim, "paused project")

    def test_graph_version_bump_blocks_new_worker_intent(self):
        with self.gateway.store._transaction() as conn:
            conn.execute("UPDATE project_state SET state_version=state_version+1 WHERE project_id=?", (self.claim.project_id,))
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(self.claim, "stale graph")

    def test_task_no_longer_running_blocks_new_worker_intent(self):
        with self.gateway.store._transaction() as conn:
            conn.execute(
                "UPDATE task_nodes SET state='QUEUED' WHERE project_id=? AND task_id=?",
                (self.claim.project_id, self.claim.task_id),
            )
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(self.claim, "task stopped")

    def test_task_objective_changed_blocks_new_worker_intent(self):
        with self.gateway.store._transaction() as conn:
            conn.execute(
                "UPDATE task_nodes SET objective_sha256=? WHERE project_id=? AND task_id=?",
                ("2" * 64, self.claim.project_id, self.claim.task_id),
            )
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(self.claim, "replaced objective")

    def test_claim_worker_identity_mismatch_blocks_new_worker_intent(self):
        forged = dataclasses.replace(self.claim, worker_id="other-worker")
        with self.assertRaisesRegex(WorkerFenceError, "WORKER_FENCED"):
            self.gateway.prepare_worker_intent(forged, "invalid claim identity")

    def test_schema_v3_upgrade_preserves_old_claim_without_giving_it_new_authority(self):
        # Drop only the 4 new columns inside an isolated temp SQLite fixture
        # to simulate an old v3 database; no user/production file is touched.
        path = self.gateway.store.path
        self.gateway.close()
        with contextlib.closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            for table in ("assignments", "project_state"):
                conn.execute(f"ALTER TABLE {table} DROP COLUMN operator_generation")
                conn.execute(f"ALTER TABLE {table} DROP COLUMN objective_generation")
            conn.execute("DELETE FROM schema_migrations WHERE version=4")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at,schema_sha256) VALUES(3,'fixture','fixture')"
            )
            conn.commit()
        upgraded = StateStore(path, [self.work])
        self.addCleanup(upgraded.close)
        with upgraded._connection() as conn:
            row = conn.execute(
                "SELECT operator_generation,objective_generation FROM assignments WHERE assignment_id=?",
                (self.claim.assignment_id,),
            ).fetchone()
            ctl = conn.execute(
                "SELECT operator_generation,objective_generation FROM project_state WHERE project_id=?",
                (self.claim.project_id,),
            ).fetchone()
        self.assertEqual((-1, -1), tuple(row))
        self.assertEqual((0, 0), tuple(ctl))
        with upgraded._connection() as conn:
            versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        self.assertIn(4, versions)
        # The old lease token remains durable, but its unbound (-1) generations
        # cannot satisfy any new worker-intent/LocalExecution authority checks.


if __name__ == "__main__":
    unittest.main()
