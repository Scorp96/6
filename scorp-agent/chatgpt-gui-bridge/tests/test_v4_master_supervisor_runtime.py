from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import pathlib
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v4_master_supervisor_runtime.py"


def load_runtime():
    spec = importlib.util.spec_from_file_location("v4_master_supervisor_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MasterSupervisorRuntimeTests(unittest.TestCase):
    def test_journal_persists_one_machine_readable_decision(self):
        runtime = load_runtime()
        decision = runtime.SupervisorDecision(
            status="RESUME_REQUIRED",
            reason="PHYSICAL_REBIND_REQUIRED",
            watchdog={"status": "RESUME_REQUIRED"},
            heartbeat=None,
            resume=None,
        )
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "decisions.jsonl"
            record = runtime.DecisionJournal(path).append(decision)
            self.assertEqual("scorp-v4-master-supervisor-decision/1", record["format"])
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record], rows)
            self.assertEqual("PHYSICAL_REBIND_REQUIRED", rows[0]["reason"])

    def test_run_supervisor_stops_and_records_unresolved_physical_rebind(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "decisions.jsonl"
            result = runtime.run_supervisor(
                _Controller([{"status": "RESUME_REQUIRED"}]),
                decision_log=path,
                interval_seconds=0,
                max_iterations=3,
                sleep=lambda _seconds: None,
            )
            self.assertEqual("RESUME_REQUIRED", result.status)
            self.assertEqual("PHYSICAL_REBIND_REQUIRED", result.stop_reason)
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual("RESUME_REQUIRED", row["status"])

    def test_supervisor_runtime_requires_existing_database(self):
        runtime = load_runtime()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            args = runtime.build_parser().parse_args(
                [
                    "--database-path", str(root / "missing.sqlite3"),
                    "--allowed-root", str(root),
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "STATE_DATABASE_MISSING"):
                runtime.run_runtime(args)
            self.assertFalse((root / "missing.sqlite3").exists())

    def test_runtime_renews_existing_master_without_browser_io(self):
        runtime = load_runtime()
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            database = root / "state.sqlite3"
            store = StateStore(database, [root])
            store.create_contract(
                "scorp-v4-master-runtime",
                root_contract={"project_id": "scorp-v4-master-runtime", "objective": "test"},
                acceptance_contract={"required": ["AC01"]},
            )
            store.start_master_session("scorp-v4-master-runtime", "master-a-runtime")
            store.close()
            log_path = root / "supervisor.jsonl"
            args = runtime.build_parser().parse_args(
                [
                    "--database-path", str(database),
                    "--allowed-root", str(root),
                    "--decision-log", str(log_path),
                    "--interval-seconds", "0",
                ]
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, runtime.run_runtime(args))
            summary = json.loads(output.getvalue())
            self.assertEqual("MASTER_ACTIVE", summary["attached"]["status"])
            self.assertEqual(0, summary["attached"]["master_epoch"])
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual("MASTER_ACTIVE", row["status"])
            self.assertFalse(row.get("rebind_enabled", False))


class _Controller:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def watchdog_once(self):
        return self.decisions.pop(0)

    def heartbeat(self):
        return {"state": "ACTIVE"}

    def resume(self):
        return {"master_epoch": 2}

    def end(self, *, reason):
        return {"state": "ENDED", "reason": reason}


if __name__ == "__main__":
    unittest.main()
