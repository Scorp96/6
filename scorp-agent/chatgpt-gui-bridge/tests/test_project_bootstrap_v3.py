import hashlib
import json
import pathlib
import tempfile
import unittest

from project_bootstrap_v3 import ProjectBootstrapV3, sha256_text
from project_state_v3 import ProjectStateStore


class ProjectBootstrapV3Tests(unittest.TestCase):
    def test_sha256_is_exact_utf8_contract_hash(self):
        text = "Root objective\nKeep immutable semantics."
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), sha256_text(text))

    def test_bootstrap_creates_contract_and_authoritative_state_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            result = ProjectBootstrapV3(root).create(
                project_id="bootstrap-live-1",
                root_objective="Ship the bounded production objective.",
                acceptance_criteria="Pass every declared acceptance gate.",
            )
            state = ProjectStateStore(root / "project-state.json").load()
            contract = json.loads((root / "project-contract.json").read_text(encoding="utf-8"))

            self.assertEqual("bootstrap-live-1", state["project_id"])
            self.assertEqual("A", state["master_identity"])
            self.assertEqual(0, state["state_version"])
            self.assertEqual("ACTIVE", state["status"])
            self.assertEqual("BOOTSTRAPPED", state["current_phase"])
            self.assertEqual("resume master A from root contract", state["next_exact_action"])
            self.assertIsNone(state["active_master_window"])
            self.assertEqual([], state["active_workers"])
            self.assertEqual(sha256_text(contract["root_objective"]), state["goal_contract_sha256"])
            self.assertEqual(sha256_text(contract["acceptance_criteria"]), state["acceptance_sha256"])
            self.assertEqual(state["goal_contract_sha256"], contract["root_objective_sha256"])
            self.assertEqual(state["acceptance_sha256"], contract["acceptance_sha256"])
            self.assertEqual(result["state"], state)
            self.assertEqual(result["contract"], contract)
            self.assertFalse((root / "master-window-lease.json").exists())

    def test_empty_contract_fields_fail_closed_without_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            bootstrap = ProjectBootstrapV3(root)
            for key, kwargs in (
                ("PROJECT_ID_EMPTY", dict(project_id="", root_objective="goal", acceptance_criteria="accept")),
                ("ROOT_OBJECTIVE_EMPTY", dict(project_id="p", root_objective="", acceptance_criteria="accept")),
                ("ACCEPTANCE_CRITERIA_EMPTY", dict(project_id="p", root_objective="goal", acceptance_criteria="")),
            ):
                with self.subTest(key=key):
                    with self.assertRaisesRegex(ValueError, key):
                        bootstrap.create(**kwargs)
                    self.assertFalse((root / "project-state.json").exists())
                    self.assertFalse((root / "project-contract.json").exists())

    def test_existing_state_or_contract_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            bootstrap = ProjectBootstrapV3(root)
            bootstrap.create(project_id="p1", root_objective="goal", acceptance_criteria="accept")
            original_state = (root / "project-state.json").read_bytes()
            original_contract = (root / "project-contract.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "PROJECT_BOOTSTRAP_ALREADY_EXISTS"):
                bootstrap.create(project_id="p2", root_objective="new goal", acceptance_criteria="new accept")
            self.assertEqual(original_state, (root / "project-state.json").read_bytes())
            self.assertEqual(original_contract, (root / "project-contract.json").read_bytes())

    def test_matching_orphan_contract_recovers_missing_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            root.mkdir(parents=True, exist_ok=True)
            contract = {
                "protocol_version": "scorp.project-contract/v1",
                "project_id": "recover",
                "master_identity": "A",
                "root_objective": "goal",
                "root_objective_sha256": sha256_text("goal"),
                "acceptance_criteria": "accept",
                "acceptance_sha256": sha256_text("accept"),
            }
            contract_path = root / "project-contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            original = contract_path.read_bytes()

            result = ProjectBootstrapV3(root).create(
                project_id="recover", root_objective="goal", acceptance_criteria="accept"
            )

            self.assertEqual(original, contract_path.read_bytes())
            self.assertTrue((root / "project-state.json").is_file())
            self.assertEqual("ACTIVE", result["state"]["status"])
            self.assertEqual(0, result["state"]["state_version"])
            self.assertEqual(contract, result["contract"])
            self.assertFalse((root / "master-window-lease.json").exists())

    def test_conflicting_orphan_contract_fails_closed_without_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            root.mkdir(parents=True, exist_ok=True)
            contract = {
                "protocol_version": "scorp.project-contract/v1",
                "project_id": "recover",
                "master_identity": "A",
                "root_objective": "goal",
                "root_objective_sha256": sha256_text("goal"),
                "acceptance_criteria": "accept",
                "acceptance_sha256": sha256_text("accept"),
            }
            contract_path = root / "project-contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            original = contract_path.read_bytes()

            with self.assertRaisesRegex(ValueError, "PROJECT_BOOTSTRAP_CONTRACT_CONFLICT"):
                ProjectBootstrapV3(root).create(
                    project_id="recover", root_objective="different goal", acceptance_criteria="accept"
                )

            self.assertEqual(original, contract_path.read_bytes())
            self.assertFalse((root / "project-state.json").exists())

    def test_contract_manifest_is_self_consistent(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            ProjectBootstrapV3(root).create(project_id="p", root_objective="goal", acceptance_criteria="accept")
            contract = json.loads((root / "project-contract.json").read_text(encoding="utf-8"))
            self.assertEqual("scorp.project-contract/v1", contract["protocol_version"])
            self.assertEqual("p", contract["project_id"])
            self.assertEqual("A", contract["master_identity"])
            self.assertEqual(hashlib.sha256(b"goal").hexdigest(), contract["root_objective_sha256"])
            self.assertEqual(hashlib.sha256(b"accept").hexdigest(), contract["acceptance_sha256"])

    def test_installer_deploys_bootstrap_module_and_template(self):
        base = pathlib.Path(__file__).resolve().parents[1]
        installer = (base / "install-bridge.ps1").read_text(encoding="utf-8")
        self.assertIn("project_bootstrap_v3.py", installer)
        self.assertIn("project-bootstrap-template.json", installer)

    def test_bootstrap_template_is_non_active_input_spec(self):
        base = pathlib.Path(__file__).resolve().parents[1]
        template = json.loads((base / "project-bootstrap-template.json").read_text(encoding="utf-8"))
        self.assertEqual("scorp.project-bootstrap/v1", template["protocol_version"])
        self.assertEqual("", template["project_id"])
        self.assertEqual("", template["root_objective"])
        self.assertEqual("", template["acceptance_criteria"])
        self.assertFalse((base / "project-state.json").exists())


if __name__ == "__main__":
    unittest.main()
