from __future__ import annotations

import pathlib
import tempfile
import threading
import unittest


class ConcurrentCasTests(unittest.TestCase):
    def make_store(self, db: pathlib.Path):
        from master_a_dynamic_v4.state_store import StateStore

        return StateStore(db, allowed_roots=[db.parent])

    def create_project(self, store):
        return store.create_contract(
            "project-ac01",
            root_contract={"objective": "prove one CAS winner"},
            acceptance_contract={"required": ["AC01"]},
        )

    def test_two_threads_on_same_version_have_exactly_one_commit(self):
        from master_a_dynamic_v4.models import CommitResult

        with tempfile.TemporaryDirectory() as td:
            db = pathlib.Path(td) / "state.sqlite3"
            seed = self.make_store(db)
            self.create_project(seed)
            seed.close()
            barrier = threading.Barrier(2)
            lock = threading.Lock()
            results = []

            def contender(index: int):
                store = self.make_store(db)
                try:
                    barrier.wait()
                    result = store.commit(
                        0,
                        0,
                        f"transition-{index}",
                        {
                            "project_id": "project-ac01",
                            "kind": "SET_PHASE",
                            "phase": f"contender-{index}",
                        },
                        [],
                    )
                    with lock:
                        results.append(result)
                finally:
                    store.close()

            threads = [threading.Thread(target=contender, args=(index,)) for index in (1, 2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())

            self.assertEqual(1, results.count(CommitResult.COMMITTED))
            self.assertEqual(1, results.count(CommitResult.VERSION_CONFLICT))
            verify = self.make_store(db)
            try:
                state = verify.get_project_state("project-ac01")
                self.assertEqual(1, state["state_version"])
                self.assertIn(state["phase"], {"contender-1", "contender-2"})
                self.assertEqual(1, verify.count_committed_transitions("project-ac01"))
            finally:
                verify.close()


if __name__ == "__main__":
    unittest.main()
