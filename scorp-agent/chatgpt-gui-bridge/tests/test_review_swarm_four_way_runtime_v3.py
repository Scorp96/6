import pathlib
import tempfile
import unittest

from project_bootstrap_v3 import ProjectBootstrapV3
from production_v3_runtime import ProductionV3Runtime


class ReviewSwarmFourWayRuntimeV3Tests(unittest.TestCase):
    def test_production_defaults_provision_four_workers_plus_master_capacity(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            ProjectBootstrapV3(root).create(
                project_id="review-four-way",
                root_objective="review one immutable git snapshot with four independent reviewers",
                acceptance_criteria="four worker generations can be in flight while one master core turn is also allowed",
            )
            runtime = ProductionV3Runtime(root, driver=object())
            coordinator = runtime._build()

            self.assertEqual(5, runtime.max_inflight)
            self.assertEqual(4, runtime.max_workers)
            self.assertEqual(5, coordinator.relay.max_inflight)
            self.assertEqual(4, coordinator.relay.max_workers)
            self.assertEqual(4, coordinator.relay.worker_pool.pool_size)

    def test_installer_registers_four_worker_five_total_inflight_capacity(self):
        installer = (pathlib.Path(__file__).resolve().parents[1] / "install-bridge.ps1").read_text(encoding="utf-8")
        self.assertIn("'--v3-max-inflight','5'", installer)
        self.assertIn("'--v3-max-workers','4'", installer)


if __name__ == "__main__":
    unittest.main()
