from __future__ import annotations

import pathlib
import tempfile
import unittest
import datetime as dt


UTC = dt.timezone.utc


class WorkerClaimRecoveryTests(unittest.TestCase):
    def _runtime(self, root: pathlib.Path):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore

        worktree = root / "worktree"
        worktree.mkdir()
        db = root / "state.sqlite3"
        store = StateStore(db, allowed_roots=[root])
        store.create_contract(
            "project-recovery",
            root_contract={"objective": "renew claims"},
            acceptance_contract={"required": ["AC-RECOVERY"]},
        )
        store.record_runtime_observation(
            "project-recovery",
            progress_state="ACTIVE_GENERATING",
            browser_semantic_state="READY",
            observed_at="2026-09-15T12:00:00Z",
            browser_succeeded=True,
        )
        scheduler = Scheduler(store, "project-recovery", PathPolicy([root]), max_workers=2)
        scheduler.enqueue_graph([
            {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "a.txt"], "dependencies": []},
        ])
        return store, scheduler

    def test_worker_lease_renews_with_fencing_and_persists_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler = self._runtime(root)
            try:
                started = dt.datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
                claim = scheduler.claim_runnable(master_epoch=0, now=started, lease_seconds=30)[0]
                renewed = scheduler.renew_worker_lease(
                    claim,
                    master_epoch=0,
                    now=started + dt.timedelta(seconds=10),
                    lease_seconds=60,
                    physical_health={
                        "assignment_id": claim.assignment_id,
                        "worker_id": claim.worker_id,
                        "session_id": "worker/worker-slot-1",
                        "status": "READY",
                        "observed_at": "2026-09-15T12:00:10Z",
                    },
                )
                self.assertEqual("ACTIVE", renewed["state"])
                self.assertEqual("2026-09-15T12:00:10Z", renewed["heartbeat_at"])
                self.assertEqual("2026-09-15T12:01:10Z", renewed["expires_at"])
                with store._connection() as conn:
                    row = conn.execute(
                        "SELECT heartbeat_at,expires_at FROM leases WHERE assignment_id=?",
                        (claim.assignment_id,),
                    ).fetchone()
                self.assertEqual("2026-09-15T12:00:10Z", row[0])
                self.assertEqual("2026-09-15T12:01:10Z", row[1])
            finally:
                store.close()

    def test_worker_lease_renewal_rejects_stale_epoch_wrong_token_and_expired_claim(self):
        from master_a_dynamic_v4.scheduler import WorkerFenceError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler = self._runtime(root)
            try:
                started = dt.datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
                claim = scheduler.claim_runnable(master_epoch=0, now=started, lease_seconds=30)[0]
                with self.assertRaisesRegex(WorkerFenceError, "MASTER_EPOCH_FENCED"):
                    scheduler.renew_worker_lease(claim, master_epoch=1, now=started + dt.timedelta(seconds=1))
                with self.assertRaisesRegex(WorkerFenceError, "LEASE_TOKEN_FENCED"):
                    scheduler.renew_worker_lease(
                        claim,
                        lease_token="wrong-token",
                        master_epoch=0,
                        now=started + dt.timedelta(seconds=1),
                    )
                with self.assertRaisesRegex(WorkerFenceError, "WORKER_LEASE_EXPIRED"):
                    scheduler.renew_worker_lease(claim, master_epoch=0, now=started + dt.timedelta(seconds=31))
                self.assertEqual("FENCED", scheduler.get_assignment(claim.assignment_id)["state"])
            finally:
                store.close()

    def test_worker_lease_renewal_requires_current_physical_health_and_generation(self):
        from master_a_dynamic_v4.scheduler import WorkerFenceError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler = self._runtime(root)
            try:
                started = dt.datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
                claim = scheduler.claim_runnable(master_epoch=0, now=started, lease_seconds=60)[0]
                with self.assertRaisesRegex(WorkerFenceError, "WORKER_PHYSICAL_HEALTH_UNVERIFIED"):
                    scheduler.renew_worker_lease(
                        claim,
                        now=started + dt.timedelta(seconds=5),
                        physical_health={
                            "assignment_id": claim.assignment_id,
                            "worker_id": claim.worker_id,
                            "session_id": "worker/worker-slot-1",
                            "status": "UNKNOWN",
                            "observed_at": "2026-09-15T12:00:05Z",
                        },
                    )
                with store._transaction() as conn:
                    conn.execute(
                        "UPDATE operator_controls SET operator_generation=operator_generation+1 WHERE project_id=?",
                        ("project-recovery",),
                    )
                with self.assertRaisesRegex(WorkerFenceError, "WORKER_OPERATOR_GENERATION_FENCED"):
                    scheduler.renew_worker_lease(
                        claim,
                        now=started + dt.timedelta(seconds=5),
                        physical_health={
                            "assignment_id": claim.assignment_id,
                            "worker_id": claim.worker_id,
                            "session_id": "worker/worker-slot-1",
                            "status": "READY",
                            "observed_at": "2026-09-15T12:00:05Z",
                        },
                    )
            finally:
                store.close()

    def test_new_master_session_fences_previous_epoch_worker_lease(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler = self._runtime(root)
            try:
                started = dt.datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
                first_master = store.start_master_session(
                    "project-recovery", "master-a", now=started, ttl_seconds=1
                )
                claim = scheduler.claim_runnable(
                    master_epoch=first_master["master_epoch"],
                    now=started,
                    lease_seconds=60,
                )[0]
                replacement = store.start_master_session(
                    "project-recovery", "master-b", now=started + dt.timedelta(seconds=2)
                )
                self.assertEqual(first_master["master_epoch"] + 1, replacement["master_epoch"])
                self.assertEqual("FENCED", scheduler.get_assignment(claim.assignment_id)["state"])
                with store._connection() as conn:
                    self.assertEqual(
                        "FENCED",
                        conn.execute(
                            "SELECT state FROM leases WHERE assignment_id=?",
                            (claim.assignment_id,),
                        ).fetchone()[0],
                    )
            finally:
                store.close()

    def test_active_claims_can_be_rehydrated_after_store_reopen(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / "worktree"
            worktree.mkdir()
            db = root / "state.sqlite3"
            store = StateStore(db, allowed_roots=[root])
            store.create_contract(
                "project-recovery",
                root_contract={"objective": "rehydrate claims"},
                acceptance_contract={"required": ["AC-RECOVERY"]},
            )
            scheduler = Scheduler(store, "project-recovery", PathPolicy([root]), max_workers=2)
            scheduler.enqueue_graph([
                {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "a.txt"], "dependencies": []},
                {"task_id": "T2", "objective_sha256": "2" * 64, "resource_scope": [worktree / "b.txt"], "dependencies": []},
            ])
            original = scheduler.claim_runnable(master_epoch=0, limit=2)
            self.assertEqual(2, len(original))
            store.close()

            reopened = StateStore(db, allowed_roots=[root])
            try:
                recovered = Scheduler(reopened, "project-recovery", PathPolicy([root]), max_workers=2).load_active_claims(master_epoch=0)
                self.assertEqual(
                    [(claim.assignment_id, claim.task_id, claim.worker_id, claim.base_state_version) for claim in original],
                    [(claim.assignment_id, claim.task_id, claim.worker_id, claim.base_state_version) for claim in recovered],
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
