from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import types
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v4_master_controller_runtime.py"


def load_runtime():
    spec = importlib.util.spec_from_file_location("v4_master_controller_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MasterControllerRuntimeTests(unittest.TestCase):
    def test_parser_accepts_only_structured_work_result_json(self):
        runtime = load_runtime()
        value = {
            "work_result_version": "1",
            "task_id": "T1",
            "status": "COMPLETE",
        }
        snapshot = "#### ChatGPT said:\n```json\n" + json.dumps(value) + "\n```"
        self.assertEqual(value, runtime.parse_structured_response(snapshot, "intent-1"))
        self.assertIsNone(runtime.parse_structured_response("#### ChatGPT said:\nfinished", "intent-1"))
        self.assertIsNone(runtime.parse_structured_response("#### ChatGPT said:\n{} trailing", "intent-1"))

    def test_parser_accepts_identity_bound_master_decision(self):
        runtime = load_runtime()
        intent_id = "master-reasoning-" + "a" * 32
        value = {
            "master_decision_version": 1,
            "project_id": "p1",
            "intent_id": intent_id,
            "master_epoch": 1,
            "base_state_version": 2,
            "operator_generation": 0,
            "objective_generation": 0,
            "input_snapshot_sha256": "b" * 64,
            "action": "WAIT",
            "reason": "existing work is still active",
        }
        snapshot = "#### ChatGPT said:\n```json\n" + json.dumps(value) + "\n```"
        self.assertEqual(value, runtime.parse_structured_response(snapshot, intent_id))
        foreign = dict(value)
        foreign["intent_id"] = "master-reasoning-" + "c" * 32
        foreign_snapshot = "#### ChatGPT said:\n" + json.dumps(foreign)
        self.assertIsNone(runtime.parse_structured_response(foreign_snapshot, intent_id))

    def test_parser_normalizes_nested_master_reasoning_binding_without_guessing(self):
        runtime = load_runtime()
        intent_id = "master-reasoning-" + "d" * 32
        binding = {
            "project_id": "p1",
            "intent_id": intent_id,
            "master_epoch": 1,
            "base_state_version": 2,
            "operator_generation": 0,
            "objective_generation": 0,
            "input_snapshot_sha256": "e" * 64,
        }
        value = {
            "master_decision_version": 1,
            "reasoning_binding": dict(binding),
            "action": "WAIT",
            "reason": "independent projector still pending",
        }
        snapshot = "#### ChatGPT said:" + chr(10) + json.dumps(value) + chr(10) + "provider footer"
        parsed = runtime.parse_structured_response(snapshot, intent_id)
        self.assertIsNotNone(parsed)
        for key, expected in binding.items():
            self.assertEqual(expected, parsed[key])
        self.assertEqual(value["reasoning_binding"], parsed["reasoning_binding"])

        conflicting = dict(value)
        conflicting["intent_id"] = intent_id
        conflicting["reasoning_binding"] = {
            **binding,
            "intent_id": "master-reasoning-" + "f" * 32,
        }
        conflict_snapshot = "#### ChatGPT said:" + chr(10) + json.dumps(conflicting)
        self.assertIsNone(runtime.parse_structured_response(conflict_snapshot, intent_id))

    def test_parser_rejects_work_result_bound_to_a_different_assignment(self):
        runtime = load_runtime()
        foreign = {
            "work_result_version": "1",
            "project_id": "foreign-project",
            "assignment_id": "assignment-foreign",
            "task_id": "T9",
            "status": "COMPLETE",
        }
        snapshot = "#### ChatGPT said:\n```json\n" + json.dumps(foreign) + "\n```"
        self.assertIsNone(
            runtime.parse_structured_response(
                snapshot, "worker-intent-assignment-current"
            )
        )
        current = dict(foreign)
        current["assignment_id"] = "assignment-current"
        current_snapshot = "#### ChatGPT said:\n```json\n" + json.dumps(current) + "\n```"
        self.assertEqual(
            current,
            runtime.parse_structured_response(
                current_snapshot, "worker-intent-assignment-current"
            ),
        )

    def test_parser_accepts_mojibake_assistant_marker_from_windows_bridge(self):
        runtime = load_runtime()
        value = {
            "work_result_version": "1",
            "project_id": "p1",
            "assignment_id": "a1",
            "task_id": "T1",
        }
        # The real Windows Chrome Use read path can decode the Chinese
        # assistant marker as this mojibake string.  The response JSON itself
        # is still intact and must be reconciled without resubmitting.
        snapshot = "#### ChatGPT ˵��:\n" + json.dumps(value) + "\n"
        self.assertEqual(value, runtime.parse_structured_response(snapshot, "intent-1"))

    def test_parser_accepts_reordered_worker_json_with_trailing_footer(self):
        runtime = load_runtime()
        intent_id = "worker-intent-assignment-reordered"
        value = {
            "acceptance_coverage": ["DECIMAL_AGGREGATE"],
            "assignment_id": "assignment-reordered",
            "base_state_version": 1,
            "candidate_commit": "a" * 40,
            "evidence": [
                {
                    "kind": "execution_request",
                    "claim": "assigned aggregate request supplied",
                }
            ],
            "objective_sha256": "b" * 64,
            "project_id": "p1",
            "result_sha256": "0" * 64,
            "scope_completed": ["DECIMAL_AGGREGATE"],
            "status": "COMPLETE",
            "task_id": "T2",
            "worker_id": "w2",
            "work_result_version": "1",
        }
        snapshot = (
            "#### ChatGPT ˵��:\n"
            + json.dumps(value, separators=(",", ":"))
            + "\nprovider footer"
        )
        self.assertEqual(
            value,
            runtime.parse_structured_response(snapshot, intent_id),
        )

    def test_worker_prompt_requires_evidence_for_complete_results(self):
        runtime = load_runtime()
        claim = types.SimpleNamespace(
            project_id="p1",
            assignment_id="a1",
            task_id="T1",
            worker_id="w1",
            slot_id="worker-slot-1",
            master_epoch=0,
            base_state_version=1,
            objective_sha256="a" * 64,
            resource_scope=("C:/lab/orders.csv",),
            access_mode="read",
            task_context={"workload": "csv_summary"},
        )
        payload = json.loads(runtime._worker_prompt(claim))
        self.assertIn("result_requirements", payload)
        self.assertIn("evidence", payload["result_requirements"]["complete_requires_nonempty"])
        self.assertIn("acceptance_coverage", payload["result_requirements"]["complete_requires_nonempty"])
        self.assertIn("scope_completed", payload["result_requirements"]["complete_requires_nonempty"])

    def test_audit_worker_prompt_forbids_invented_local_execution(self):
        runtime = load_runtime()
        claim = types.SimpleNamespace(
            project_id="p1",
            assignment_id="a1",
            task_id="T1",
            worker_id="w1",
            slot_id="worker-slot-1",
            master_epoch=0,
            base_state_version=1,
            objective_sha256="a" * 64,
            resource_scope=("C:/lab/source_bundle.txt",),
            access_mode="read",
            task_context={"candidate_commit": "b" * 40, "instruction": "audit only"},
        )
        payload = json.loads(runtime._worker_prompt(claim))
        requirements = payload["result_requirements"]
        self.assertFalse(requirements["execution_request_required_for_complete"])
        self.assertTrue(requirements["execution_request_forbidden_without_template"])
        joined = " ".join(payload["instructions"])
        self.assertIn("Omit execution_request", joined)
        self.assertNotIn("supplying execution_request", joined)

    def test_execution_worker_prompt_does_not_require_local_file_access(self):
        runtime = load_runtime()
        claim = types.SimpleNamespace(
            project_id="p1",
            assignment_id="a1",
            task_id="T1",
            worker_id="w1",
            slot_id="worker-slot-1",
            master_epoch=0,
            base_state_version=1,
            objective_sha256="a" * 64,
            resource_scope=("C:/lab/orders.csv",),
            access_mode="read",
            task_context={
                "candidate_commit": "b" * 40,
                "execution_request_template": {
                    "module": "master_a_dynamic_v4.csv_workload.cli",
                    "args": ["C:/lab/orders.csv", "--operation", "validate"],
                    "working_directory": "C:/lab",
                    "resource_paths": ["C:/lab/orders.csv"],
                    "access_mode": "read",
                    "timeout_seconds": 60,
                },
            },
        )
        payload = json.loads(runtime._worker_prompt(claim))
        joined = " ".join(str(item) for item in payload["instructions"])
        self.assertIn("does not execute LocalExecution", joined)
        self.assertIn("must not read, modify, or probe the local resource itself", joined)
        self.assertIn("COMPLETE means the bounded execution request is fully prepared", joined)
        self.assertIn(
            "Do not return BLOCKED merely because this chat cannot access the local filesystem",
            joined,
        )
        self.assertIn("Do not claim that the file was read or the command ran", joined)

    def test_worker_prompt_requires_candidate_commit_and_result_hash(self):
        runtime = load_runtime()
        claim = types.SimpleNamespace(
            project_id="p1",
            assignment_id="a1",
            task_id="T1",
            worker_id="w1",
            slot_id="worker-slot-1",
            master_epoch=0,
            base_state_version=1,
            objective_sha256="a" * 64,
            resource_scope=("C:/lab/orders.csv",),
            access_mode="read",
            task_context={"candidate_commit": "b" * 40},
        )
        payload = json.loads(runtime._worker_prompt(claim))
        required = payload["result_requirements"]
        self.assertTrue(required["candidate_commit_required"])
        self.assertTrue(required["result_sha256_required"])
        self.assertIn("candidate_commit", payload["instructions"][-1])
        self.assertIn("result_sha256", payload["instructions"][-1])

    def test_worker_prompt_requires_json_safe_structured_evidence(self):
        runtime = load_runtime()
        claim = types.SimpleNamespace(
            project_id="p1", assignment_id="a1", task_id="T2", worker_id="w2",
            slot_id="worker-slot-2", master_epoch=1, base_state_version=1,
            objective_sha256="a" * 64, resource_scope=("C:/lab/t2/input.csv",),
            access_mode="read", task_context={
                "candidate_commit": "b" * 40,
                "execution_request_template": {
                    "module": "master_a_dynamic_v4.csv_workload.cli",
                    "args": ["input.csv", "--operation", "aggregate"],
                    "working_directory": "C:/lab/t2",
                    "resource_paths": ["C:/lab/t2/input.csv"],
                    "access_mode": "read", "timeout_seconds": 60,
                },
            },
        )
        payload = json.loads(runtime._worker_prompt(claim))
        safety = payload["result_requirements"]["json_safety"]
        self.assertEqual("object", safety["evidence_item_type"])
        self.assertEqual(["kind", "claim"], safety["evidence_required_keys"])
        self.assertTrue(safety["forbid_execution_request_restatement_in_strings"])
        self.assertTrue(safety["forbid_json_syntax_inside_free_text_strings"])
        example = safety["evidence_example"]
        self.assertEqual({"kind": "execution_request", "claim": "assigned request supplied"}, example)
        joined = " ".join(payload["instructions"])
        self.assertIn("evidence must be an array of JSON objects", joined)
        self.assertIn("Do not restate execution_request.args", joined)

    def test_runtime_requires_explicit_send_gate_before_opening_browser(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            args = runtime.build_parser().parse_args(
                [
                    "--plan-json", str(root / "plan.json"),
                    "--database-path", str(root / "state.sqlite3"),
                    "--driver-state-path", str(root / "driver.json"),
                    "--allowed-root", str(root),
                ]
            )
            self.assertEqual(2, runtime.run_runtime(args))
            self.assertFalse((root / "state.sqlite3").exists())
            self.assertFalse((root / "driver.json").exists())

    def test_send_requires_exact_candidate_manifest_before_opening_browser(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({
                    "root_contract": {"project_id": "scorp-v4-master-runtime", "root_objective": "test"},
                    "acceptance_contract": {"required": ["AC01"]},
                    "plan": {"project_id": "scorp-v4-master-runtime", "master_identity": "A", "tasks": []},
                }),
                encoding="utf-8",
            )
            args = runtime.build_parser().parse_args(
                [
                    "--send",
                    "--plan-json", str(plan),
                    "--database-path", str(root / "state.sqlite3"),
                    "--driver-state-path", str(root / "driver.json"),
                    "--allowed-root", str(root),
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "CANDIDATE_BINDING_REQUIRED"):
                runtime.run_runtime(args)
            self.assertFalse((root / "state.sqlite3").exists())
            self.assertFalse((root / "driver.json").exists())

    def test_candidate_binding_accepts_canonical_manifest_hash(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            core = {
                "format": "scorp-v4-candidate-manifest/1",
                "candidate_commit": "a" * 40,
                "source_tree": str(root),
                "files": {"example.py": {"sha256": "b" * 64, "size": 1}},
            }
            from master_a_dynamic_v4.models import sha256_json
            manifest = {**core, "manifest_sha256": sha256_json(core)}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            binding = runtime.validate_candidate_binding(
                path,
                candidate_commit="a" * 40,
                manifest_sha256=manifest["manifest_sha256"],
            )
            self.assertEqual(manifest["manifest_sha256"], binding["manifest_sha256"])


    def test_worker_prompt_normalizes_execution_template_windows_paths_for_json_copy(self):
        runtime = load_runtime()
        original_template = {
            "module": "master_a_dynamic_v4.csv_workload.cli",
            "args": ["input.csv", "--operation", "validate"],
            "working_directory": r"C:\ScorpAgent\lab\t1",
            "resource_paths": [r"C:\ScorpAgent\lab\t1\input.csv"],
            "access_mode": "read",
            "timeout_seconds": 60,
        }
        claim = types.SimpleNamespace(
            project_id="p1", assignment_id="a1", task_id="T1", worker_id="w1",
            slot_id="worker-slot-1", master_epoch=0, base_state_version=1,
            objective_sha256="a" * 64, resource_scope=(r"C:\ScorpAgent\lab\t1",),
            access_mode="read", task_context={"candidate_commit": "b" * 40, "execution_request_template": original_template},
        )
        payload = json.loads(runtime._worker_prompt(claim))
        template = payload["task_context"]["execution_request_template"]
        self.assertEqual("C:/ScorpAgent/lab/t1", template["working_directory"])
        self.assertEqual(["C:/ScorpAgent/lab/t1/input.csv"], template["resource_paths"])
        self.assertEqual(r"C:\ScorpAgent\lab\t1", original_template["working_directory"])


    def test_worker_result_normalization_defaults_only_optional_list_fields(self):
        from master_a_dynamic_v4.master_controller import normalize_worker_result_envelope
        captured = {
            "work_result_version": 1,
            "project_id": "p",
            "assignment_id": "a",
            "task_id": "T1",
            "worker_id": "w",
            "objective_sha256": "a" * 64,
            "base_state_version": 1,
            "candidate_commit": "b" * 40,
            "status": "COMPLETE",
            "scope_completed": ["input.csv"],
            "evidence": ["execution requested"],
            "acceptance_coverage": ["AC1"],
            "scope_not_completed": None,
        }
        normalized = normalize_worker_result_envelope(captured)
        self.assertEqual([], normalized["scope_not_completed"])
        for field in ("deliverables", "facts", "inferences", "unknowns", "contradictions", "followup_proposals"):
            self.assertEqual([], normalized[field])
        self.assertEqual(["input.csv"], normalized["scope_completed"])
        self.assertEqual(["execution requested"], normalized["evidence"])
        self.assertEqual(["AC1"], normalized["acceptance_coverage"])

    def test_worker_result_normalization_does_not_invent_required_complete_lists(self):
        from master_a_dynamic_v4.master_controller import normalize_worker_result_envelope
        normalized = normalize_worker_result_envelope({"status": "COMPLETE"})
        self.assertNotIn("scope_completed", normalized)
        self.assertNotIn("evidence", normalized)
        self.assertNotIn("acceptance_coverage", normalized)


if __name__ == "__main__":
    unittest.main()
