from __future__ import annotations

import pathlib
import tempfile
import unittest


class WorkerClaimRecoveryTests(unittest.TestCase):
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
