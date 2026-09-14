import datetime as dt
import json
import pathlib
import tempfile
import unittest

from continuation_watchdog_v3 import ContinuationWatchdog
from master_window_lease_v3 import MasterWindowLeaseStore
from production_v3_runtime import ProductionV3Runtime
from project_bootstrap_v3 import ProjectBootstrapV3
from project_state_v3 import ProjectStateStore


UTC = dt.timezone.utc


class ProductionV3ContractContextTests(unittest.TestCase):
    def _bootstrap(self, root):
        return ProjectBootstrapV3(root).create(
            project_id="production-contract-context",
            root_objective="Dispatch one bounded worker, integrate its handoff, then finish the production acceptance canary.",
            acceptance_criteria="A receives the immutable contract text; worker handoff is integrated; project reaches COMPLETE without changing contract hashes.",
        )

    def test_watchdog_resume_carries_validated_project_contract_text(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            created = self._bootstrap(root)
            state = ProjectStateStore(root / "project-state.json")
            leases = MasterWindowLeaseStore(root / "master-window-lease.json")
            watchdog = ContinuationWatchdog(
                state,
                leases,
                root / "master-worker-v3-outbox",
                project_contract=created["contract"],
            )
            result = watchdog.run_once(now=dt.datetime(2026, 9, 12, 1, 0, tzinfo=UTC))
            self.assertEqual("QUEUED", result["status"])
            turn = json.loads(
                (root / "master-worker-v3-outbox" / f"{result['turn_id']}.json").read_text(encoding="utf-8")
            )
            contract = turn["payload"]["project_contract"]
            self.assertEqual(created["contract"], contract)
            self.assertEqual(turn["root_objective_sha256"], contract["root_objective_sha256"])
            self.assertEqual(turn["acceptance_sha256"], contract["acceptance_sha256"])

    def test_production_runtime_passes_validated_contract_to_watchdog(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            created = self._bootstrap(root)
            runtime = ProductionV3Runtime(root, driver=object())
            coordinator = runtime._build()
            self.assertIsNotNone(coordinator)
            self.assertEqual(created["contract"], coordinator.watchdog.project_contract)


if __name__ == "__main__":
    unittest.main()
