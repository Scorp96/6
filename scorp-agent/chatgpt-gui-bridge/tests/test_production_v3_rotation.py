import pathlib
import shutil
import tempfile
import unittest

from production_v3_runtime import ProductionV3Runtime
from project_bootstrap_v3 import ProjectBootstrapV3
from project_lifecycle_v3 import ProjectLifecycleV3
from project_state_v3 import ProjectStateStore


class ProductionV3RotationTests(unittest.IsolatedAsyncioTestCase):
    def _bootstrap(self, root, project_id):
        ProjectBootstrapV3(root).create(
            project_id=project_id,
            root_objective=f"goal for {project_id}",
            acceptance_criteria=f"acceptance for {project_id}",
        )

    def _complete(self, root):
        store = ProjectStateStore(pathlib.Path(root) / "project-state.json")
        current = store.load()
        return store.update(
            current["state_version"],
            {
                "status": "COMPLETE",
                "current_phase": "COMPLETE",
                "next_exact_action": "archive terminal project",
                "active_master_window": None,
                "active_workers": [],
            },
        )

    async def test_empty_active_root_forgets_cached_project(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            self._bootstrap(active, "p1")
            runtime = ProductionV3Runtime(active, driver=object())
            self.assertIsNotNone(runtime._build())
            self.assertEqual("p1", runtime._project_id)

            shutil.rmtree(active)
            active.mkdir(parents=True)
            result = await runtime.run_once()

            self.assertEqual({"status": "IDLE", "runtime": "V3"}, result)
            self.assertIsNone(runtime._coordinator)
            self.assertIsNone(runtime._project_id)

            self._bootstrap(active, "p2")
            self._complete(active)
            result = await runtime.run_once()
            self.assertEqual("p2", runtime._project_id)
            self.assertEqual("COMPLETE", result["project_status"])

    async def test_arbitrary_project_replacement_without_rotation_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            self._bootstrap(active, "p1")
            runtime = ProductionV3Runtime(active, driver=object())
            runtime._build()

            shutil.rmtree(active)
            active.mkdir(parents=True)
            self._bootstrap(active, "p2")
            self._complete(active)

            with self.assertRaisesRegex(ValueError, "PRODUCTION_V3_PROJECT_CHANGED"):
                await runtime.run_once()

    async def test_archived_rotation_marker_allows_immediate_next_project_without_restart(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            archive = base / "archive"
            self._bootstrap(active, "p1")
            runtime = ProductionV3Runtime(active, driver=object())
            runtime._build()
            self._complete(active)

            archived = ProjectLifecycleV3(active, archive_root=archive).archive_terminal()
            self.assertEqual("p1", archived["project_id"])

            self._bootstrap(active, "p2")
            self._complete(active)
            result = await runtime.run_once()

            self.assertEqual("p2", runtime._project_id)
            self.assertEqual("IDLE", result["status"])
            self.assertEqual("V3", result["runtime"])
            self.assertEqual("p2", result["project_id"])
            self.assertEqual("COMPLETE", result["project_status"])

    async def test_rotation_marker_for_other_project_does_not_authorize_switch(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            active = base / "active"
            archive = base / "archive"
            self._bootstrap(active, "p1")
            runtime = ProductionV3Runtime(active, driver=object())
            runtime._build()
            self._complete(active)
            ProjectLifecycleV3(active, archive_root=archive).archive_terminal()

            marker_path = base / "project-rotation.json"
            marker = marker_path.read_text(encoding="utf-8")
            marker_path.write_text(marker.replace('"p1"', '"not-p1"', 1), encoding="utf-8")

            self._bootstrap(active, "p2")
            self._complete(active)
            with self.assertRaisesRegex(ValueError, "PRODUCTION_V3_PROJECT_CHANGED"):
                await runtime.run_once()


if __name__ == "__main__":
    unittest.main()
