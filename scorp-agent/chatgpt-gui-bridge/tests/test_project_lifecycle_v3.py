import json
import pathlib
import tempfile
import unittest

from project_bootstrap_v3 import ProjectBootstrapV3
from project_lifecycle_v3 import ProjectLifecycleV3
from project_state_v3 import ProjectStateStore


class ProjectLifecycleV3Tests(unittest.TestCase):
    def _bootstrap(self, root, project_id="p1"):
        ProjectBootstrapV3(root).create(
            project_id=project_id,
            root_objective=f"goal for {project_id}",
            acceptance_criteria=f"accept {project_id}",
        )

    def _terminal(self, root, status="COMPLETE", *, active_workers=None):
        store = ProjectStateStore(pathlib.Path(root) / "project-state.json")
        return store.update(
            0,
            {
                "status": status,
                "current_phase": status,
                "next_exact_action": "archive terminal project",
                "active_workers": list(active_workers or []),
            },
        )

    def test_active_project_cannot_be_archived(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            self._bootstrap(active)
            with self.assertRaisesRegex(ValueError, "PROJECT_ARCHIVE_STATUS_NOT_TERMINAL"):
                ProjectLifecycleV3(active, archive_root=base / "archive").archive_terminal()
            self.assertTrue((active / "project-state.json").is_file())

    def test_terminal_project_with_active_actor_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            self._bootstrap(active)
            self._terminal(active, active_workers=[{"worker_id": "worker-live"}])
            with self.assertRaisesRegex(ValueError, "PROJECT_ARCHIVE_ACTIVE_ACTORS"):
                ProjectLifecycleV3(active, archive_root=base / "archive").archive_terminal()
            self.assertTrue((active / "project-state.json").is_file())

    def test_complete_project_moves_whole_root_and_writes_rotation_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            archive = base / "archive"
            self._bootstrap(active, "complete-p")
            terminal = self._terminal(active, "COMPLETE")
            (active / "sessions-v3.json").write_text('{"kept":true}', encoding="utf-8")

            result = ProjectLifecycleV3(active, archive_root=archive).archive_terminal()

            archived = pathlib.Path(result["archive_path"])
            self.assertTrue(active.is_dir())
            self.assertFalse((active / "project-state.json").exists())
            self.assertTrue((archived / "project-state.json").is_file())
            self.assertTrue((archived / "project-contract.json").is_file())
            self.assertTrue((archived / "sessions-v3.json").is_file())
            manifest = json.loads((archived / "archive-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("scorp.project-archive/v1", manifest["protocol_version"])
            self.assertEqual("complete-p", manifest["project_id"])
            self.assertEqual("COMPLETE", manifest["status"])
            self.assertEqual(terminal["state_version"], manifest["state_version"])
            marker = json.loads((base / "project-rotation.json").read_text(encoding="utf-8"))
            self.assertEqual("scorp.project-rotation/v1", marker["protocol_version"])
            self.assertEqual("ARCHIVED", marker["phase"])
            self.assertEqual("complete-p", marker["previous_project_id"])
            self.assertEqual(str(archived), marker["archive_path"])

    def test_hard_blocked_project_can_be_archived(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            self._bootstrap(active, "blocked-p")
            self._terminal(active, "HARD_BLOCKED")
            result = ProjectLifecycleV3(active, archive_root=base / "archive").archive_terminal()
            self.assertEqual("HARD_BLOCKED", result["status"])
            self.assertFalse((active / "project-state.json").exists())

    def test_prepared_rotation_can_be_finalized_after_directory_move_crash(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            archive = base / "archive"
            self._bootstrap(active, "recover-archive")
            state = self._terminal(active, "COMPLETE")
            archive.mkdir(parents=True, exist_ok=True)
            destination = archive / "prepared-destination"
            marker = {
                "protocol_version": "scorp.project-rotation/v1",
                "phase": "PREPARED",
                "previous_project_id": state["project_id"],
                "previous_goal_contract_sha256": state["goal_contract_sha256"],
                "previous_acceptance_sha256": state["acceptance_sha256"],
                "previous_state_version": state["state_version"],
                "archive_path": str(destination),
                "prepared_at": "2026-01-01T00:00:00+00:00",
            }
            (base / "project-rotation.json").write_text(json.dumps(marker), encoding="utf-8")
            active.replace(destination)

            result = ProjectLifecycleV3(active, archive_root=archive).archive_terminal()

            self.assertTrue(result["recovered"])
            self.assertTrue(active.is_dir())
            final = json.loads((base / "project-rotation.json").read_text(encoding="utf-8"))
            self.assertEqual("ARCHIVED", final["phase"])
            self.assertEqual("recover-archive", final["previous_project_id"])

    def test_installer_deploys_lifecycle_module(self):
        base = pathlib.Path(__file__).resolve().parents[1]
        installer = (base / "install-bridge.ps1").read_text(encoding="utf-8")
        self.assertIn("project_lifecycle_v3.py", installer)


if __name__ == "__main__":
    unittest.main()
