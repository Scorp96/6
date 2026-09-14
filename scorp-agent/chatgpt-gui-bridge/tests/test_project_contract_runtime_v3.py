import json
import pathlib
import tempfile
import unittest

from production_v3_runtime import ProductionV3Runtime
from project_bootstrap_v3 import ProjectBootstrapV3


class ProjectContractRuntimeV3Tests(unittest.TestCase):
    def _bootstrapped(self, td):
        root = pathlib.Path(td)
        ProjectBootstrapV3(root).create(
            project_id="contract-runtime",
            root_objective="Preserve the immutable root objective.",
            acceptance_criteria="Reject any contract drift before Master A starts.",
        )
        return root

    def test_matching_contract_builds_runtime_without_gui_submission(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._bootstrapped(td)
            runtime = ProductionV3Runtime(root, driver=object())
            coordinator = runtime._build()
            self.assertIsNotNone(coordinator)
            self.assertEqual("contract-runtime", runtime._project_id)

    def test_root_objective_text_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._bootstrapped(td)
            path = root / "project-contract.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["root_objective"] += " TAMPERED"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PROJECT_CONTRACT_GOAL_HASH_MISMATCH"):
                ProductionV3Runtime(root, driver=object())._build()

    def test_acceptance_hash_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._bootstrapped(td)
            path = root / "project-contract.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["acceptance_sha256"] = "0" * 64
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PROJECT_CONTRACT_ACCEPTANCE_HASH_MISMATCH"):
                ProductionV3Runtime(root, driver=object())._build()

    def test_project_identity_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._bootstrapped(td)
            path = root / "project-contract.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["project_id"] = "other-project"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PROJECT_CONTRACT_PROJECT_MISMATCH"):
                ProductionV3Runtime(root, driver=object())._build()

    def test_legacy_state_without_contract_remains_supported(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._bootstrapped(td)
            (root / "project-contract.json").unlink()
            runtime = ProductionV3Runtime(root, driver=object())
            self.assertIsNotNone(runtime._build())


if __name__ == "__main__":
    unittest.main()
