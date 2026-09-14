from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import types
import unittest


class TaskContextPropagationTests(unittest.TestCase):
    def test_task_context_is_persisted_in_claim_and_restart_recovery(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            db = root / "state.sqlite3"
            source = root / "orders.csv"
            source.write_text("category,amount\na,1.20\n", encoding="utf-8")
            context = {
                "workload": "csv_summary",
                "instruction": "Read the assigned CSV and return a structured WORK_RESULT/1.",
                "expected_artifacts": [str(source)],
            }
            with StateStore(db, [root]) as store:
                store.create_contract(
                    "context-project",
                    root_contract={"objective": "context"},
                    acceptance_contract={"required": ["AC_CONTEXT"]},
                )
                scheduler = Scheduler(store, "context-project", PathPolicy([root]), max_workers=2)
                scheduler.enqueue_graph(
                    [{
                        "task_id": "T1",
                        "objective_sha256": "a" * 64,
                        "resource_scope": [source],
                        "access_mode": "read",
                        "dependencies": [],
                        "task_context": context,
                    }]
                )
                master = store.start_master_session("context-project", "master-context")
                claims = scheduler.claim_runnable(master_epoch=master["master_epoch"], limit=1)
                self.assertEqual(1, len(claims))
                self.assertEqual(context, dict(claims[0].task_context))
            with StateStore(db, [root]) as reopened:
                scheduler = Scheduler(reopened, "context-project", PathPolicy([root]), max_workers=2)
                recovered = scheduler.load_active_claims(master_epoch=0)
                self.assertEqual(1, len(recovered))
                self.assertEqual(context, dict(recovered[0].task_context))

    def test_task_context_must_be_a_mapping(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler, SchedulerError
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            with StateStore(root / "state.sqlite3", [root]) as store:
                store.create_contract(
                    "invalid-context-project",
                    root_contract={"objective": "context"},
                    acceptance_contract={"required": ["AC_CONTEXT"]},
                )
                scheduler = Scheduler(store, "invalid-context-project", PathPolicy([root]), max_workers=2)
                with self.assertRaisesRegex(SchedulerError, "TASK_CONTEXT_INVALID"):
                    scheduler.enqueue_graph([{
                        "task_id": "T1",
                        "objective_sha256": "a" * 64,
                        "resource_scope": [root / "out.txt"],
                        "dependencies": [],
                        "task_context": ["not", "a", "mapping"],
                    }])


class WorkerPromptContextTests(unittest.TestCase):
    def test_runtime_prompt_includes_durable_task_context(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        path = root / "scorp-agent" / "chatgpt-gui-bridge" / "tools" / "v4_master_controller_runtime.py"
        spec = importlib.util.spec_from_file_location("scorp_v4_master_runtime_for_test", path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        claim = types.SimpleNamespace(
            project_id="context-project",
            assignment_id="assignment-1",
            task_id="T1",
            worker_id="worker-1",
            slot_id="worker-slot-1",
            master_epoch=0,
            base_state_version=3,
            objective_sha256="a" * 64,
            resource_scope=("C:/lab/orders.csv",),
            access_mode="read",
            task_context={"workload": "csv_summary", "instruction": "Return WORK_RESULT/1."},
        )
        payload = json.loads(module._worker_prompt(claim))
        self.assertEqual(claim.task_context, payload["task_context"])


if __name__ == "__main__":
    unittest.main()

class ControllerTaskContextTests(unittest.TestCase):
    def test_plan_accepts_structured_task_context(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        controller = MasterAController(types.SimpleNamespace(project_id="context-project"), "master-context")
        normalized = controller._validate_plan({
            "project_id": "context-project",
            "master_identity": "A",
            "tasks": [{
                "task_id": "T1",
                "objective_sha256": "a" * 64,
                "resource_scope": ["C:/lab/orders.csv"],
                "access_mode": "read",
                "dependencies": [],
                "acceptance_criteria_ids": ["AC_CONTEXT"],
                "task_context": {"instruction": "Return WORK_RESULT/1."},
            }],
        })
        self.assertEqual({"instruction": "Return WORK_RESULT/1."}, normalized["tasks"][0]["task_context"])
