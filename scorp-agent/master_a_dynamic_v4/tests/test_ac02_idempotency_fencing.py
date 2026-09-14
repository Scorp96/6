from __future__ import annotations

import pathlib
import tempfile
import unittest


class IdempotencyFencingTests(unittest.TestCase):
    def make_store(self, root: pathlib.Path):
        from master_a_dynamic_v4.state_store import StateStore

        store = StateStore(root / "state.sqlite3", allowed_roots=[root])
        store.create_contract(
            "project-ac02",
            root_contract={"objective": "prove idempotency and fencing"},
            acceptance_contract={"required": ["AC02"]},
        )
        return store

    def proposal(self, phase="GREEN"):
        return {"project_id": "project-ac02", "kind": "SET_PHASE", "phase": phase}

    def test_identical_transition_replay_is_idempotent_but_changed_content_is_rejected(self):
        from master_a_dynamic_v4.models import CommitResult

        with tempfile.TemporaryDirectory() as td:
            store = self.make_store(pathlib.Path(td))
            try:
                first = store.commit(0, 0, "same-transition", self.proposal(), [])
                replay = store.commit(0, 0, "same-transition", self.proposal(), [])
                conflict = store.commit(0, 0, "same-transition", self.proposal("CHANGED"), [])
                self.assertEqual(CommitResult.COMMITTED, first)
                self.assertEqual(CommitResult.ALREADY_COMMITTED, replay)
                self.assertEqual(CommitResult.REJECTED, conflict)
                self.assertEqual(1, store.get_project_state("project-ac02")["state_version"])
                self.assertEqual(1, store.count_committed_transitions("project-ac02"))
            finally:
                store.close()

    def test_old_epoch_is_fenced_and_stale_version_conflicts(self):
        from master_a_dynamic_v4.models import CommitResult

        with tempfile.TemporaryDirectory() as td:
            store = self.make_store(pathlib.Path(td))
            try:
                self.assertEqual(1, store.advance_master_epoch("project-ac02", expected_epoch=0))
                self.assertEqual(
                    CommitResult.FENCED,
                    store.commit(0, 0, "old-epoch", self.proposal("OLD"), []),
                )
                self.assertEqual(
                    CommitResult.COMMITTED,
                    store.commit(0, 1, "current-epoch", self.proposal("CURRENT"), []),
                )
                self.assertEqual(
                    CommitResult.VERSION_CONFLICT,
                    store.commit(0, 1, "stale-version", self.proposal("STALE"), []),
                )
            finally:
                store.close()

    def test_invalid_master_identity_and_unrecognized_proposal_are_rejected(self):
        from master_a_dynamic_v4.models import CommitResult

        with tempfile.TemporaryDirectory() as td:
            store = self.make_store(pathlib.Path(td))
            try:
                wrong_master = dict(self.proposal(), master_identity="B")
                unknown = {"project_id": "project-ac02", "kind": "DIRECT_SQL", "sql": "DELETE"}
                self.assertEqual(CommitResult.REJECTED, store.commit(0, 0, "wrong-master", wrong_master, []))
                self.assertEqual(CommitResult.REJECTED, store.commit(0, 0, "direct-sql", unknown, []))
                self.assertEqual(0, store.get_project_state("project-ac02")["state_version"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
