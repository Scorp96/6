import pathlib
import tempfile
import unittest

from project_bootstrap_v3 import ProjectBootstrapV3
from production_v3_runtime import ProductionV3Runtime
from worker_conversation_pool_v3 import WorkerConversationPoolV3


class ProductionWorkerPoolWiringV3Tests(unittest.TestCase):
    def test_runtime_injects_durable_worker_pool_into_parallel_relay(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            ProjectBootstrapV3(root).create(
                project_id="prod-worker-pool",
                root_objective="objective",
                acceptance_criteria="acceptance",
            )
            runtime = ProductionV3Runtime(root, driver=object())
            coordinator = runtime._build()
            pool = coordinator.relay.worker_pool
            self.assertIsInstance(pool, WorkerConversationPoolV3)
            self.assertEqual(root / "worker-conversation-pool-v3.json", pool.path)
            self.assertEqual(4, pool.pool_size)

    def test_installer_packages_worker_conversation_pool(self):
        installer = (pathlib.Path(__file__).resolve().parents[1] / "install-bridge.ps1").read_text(encoding="utf-8")
        self.assertIn("worker_conversation_pool_v3.py", installer)


if __name__ == "__main__":
    unittest.main()
