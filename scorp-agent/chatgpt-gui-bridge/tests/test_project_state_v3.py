import json
import pathlib
import tempfile
import unittest

from project_state_v3 import ProjectStateStore


class ProjectStateV3Tests(unittest.TestCase):
    def _initial(self):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "social-cbi-production",
            "master_identity": "A",
            "state_version": 0,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "LIVE_BROWSER_ACCEPTANCE",
            "next_exact_action": "run guarded live canary",
            "active_master_window": None,
            "active_workers": [],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

    def test_create_and_restart_load_are_identical(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "project-state.json"
            store = ProjectStateStore(path)
            created = store.create(self._initial())
            reloaded = ProjectStateStore(path).load()
            self.assertEqual(created, reloaded)
            self.assertEqual(0, reloaded["state_version"])
            self.assertEqual("A", reloaded["master_identity"])

    def test_compare_and_swap_increments_exactly_once(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "project-state.json"
            store = ProjectStateStore(path)
            store.create(self._initial())
            updated = store.update(0, {"current_phase": "ENTITY_RESOLUTION", "next_exact_action": "run resolver tests"})
            self.assertEqual(1, updated["state_version"])
            self.assertEqual("ENTITY_RESOLUTION", updated["current_phase"])
            with self.assertRaisesRegex(ValueError, "PROJECT_STATE_VERSION_CONFLICT"):
                store.update(0, {"current_phase": "STALE_WRITE"})

    def test_goal_and_acceptance_contracts_are_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "project-state.json"
            store = ProjectStateStore(path)
            store.create(self._initial())
            with self.assertRaisesRegex(ValueError, "PROJECT_GOAL_IMMUTABLE"):
                store.update(0, {"goal_contract_sha256": "x" * 64})
            with self.assertRaisesRegex(ValueError, "PROJECT_ACCEPTANCE_IMMUTABLE"):
                store.update(0, {"acceptance_sha256": "y" * 64})

    def test_terminal_state_cannot_return_to_active(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "project-state.json"
            store = ProjectStateStore(path)
            store.create(self._initial())
            done = store.update(0, {"status": "COMPLETE"})
            self.assertEqual("COMPLETE", done["status"])
            with self.assertRaisesRegex(ValueError, "PROJECT_STATE_TERMINAL"):
                store.update(1, {"status": "ACTIVE"})

    def test_master_identity_is_always_a(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "project-state.json"
            initial = self._initial()
            initial["master_identity"] = "B"
            with self.assertRaisesRegex(ValueError, "MASTER_IDENTITY_INVALID"):
                ProjectStateStore(path).create(initial)


if __name__ == "__main__":
    unittest.main()
