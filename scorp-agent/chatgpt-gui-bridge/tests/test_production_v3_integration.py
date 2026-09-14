import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from bridge_worker import BridgeRuntime


class EmptyGitHub:
    def list_comments(self, issue_number):
        return []


class FakeV3Runtime:
    def __init__(self, result):
        self.result = dict(result)
        self.calls = 0

    async def run_once(self):
        self.calls += 1
        return dict(self.result)


class ProductionV3IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _roots(self, td):
        root = Path(td)
        orch = root / "orch"
        state = root / "state"
        bridge = root / "bridge"
        orch.mkdir(); state.mkdir(); bridge.mkdir()
        (orch / "registry.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
        (orch / "continuation-outbox").mkdir()
        return root, orch, state, bridge

    async def test_v3_runtime_runs_before_legacy_role_relay(self):
        self.assertIn("v3_runtime", inspect.signature(BridgeRuntime.__init__).parameters)
        with tempfile.TemporaryDirectory() as td:
            root, orch, state, bridge = self._roots(td)
            role_calls = []

            async def legacy_gui(*args, **kwargs):
                raise AssertionError("legacy continuation must not run")

            async def role_gui(*args, **kwargs):
                role_calls.append(1)
                raise AssertionError("legacy role relay must not run when V3 is active")

            v3 = FakeV3Runtime({"status": "TICK", "project_id": "prod-v3"})
            runtime = BridgeRuntime(
                orch, state, bridge, EmptyGitHub(), legacy_gui, "Scorp96",
                role_gui_turn=role_gui, v3_runtime=v3,
            )
            result = await runtime.run_once()
            self.assertEqual("TICK", result["status"])
            self.assertEqual(1, v3.calls)
            self.assertEqual([], role_calls)

    async def test_v3_idle_falls_back_to_legacy_role_relay(self):
        self.assertIn("v3_runtime", inspect.signature(BridgeRuntime.__init__).parameters)
        with tempfile.TemporaryDirectory() as td:
            root, orch, state, bridge = self._roots(td)
            role_calls = []

            async def legacy_gui(*args, **kwargs):
                raise AssertionError("legacy continuation must not run")

            async def role_gui(*args, **kwargs):
                role_calls.append(1)
                return ({"kind": "TERMINAL", "state": "DONE"}, "snapshot", "https://chatgpt.com/c/prod-v3-fallback")

            outbox = bridge / "role-relay-outbox"
            outbox.mkdir()
            turn = {
                "protocol_version": "scorp.gui-role-relay/turn-v1",
                "turn_id": "turn-a-prod-fallback",
                "mission_id": "mission-prod-fallback",
                "generation": 1,
                "from_role": "SYSTEM",
                "to_role": "A",
                "role_kind": "CONTROLLER",
                "root_objective_sha256": "a" * 64,
                "acceptance_sha256": "b" * 64,
                "role_turn_budget_seconds": 1500,
                "handoff_reserve_seconds": 300,
                "payload": {"event": "MISSION_BOOTSTRAP", "objective": "fallback"},
            }
            (outbox / "turn-a-prod-fallback.json").write_text(json.dumps(turn), encoding="utf-8")

            v3 = FakeV3Runtime({"status": "IDLE"})
            runtime = BridgeRuntime(
                orch, state, bridge, EmptyGitHub(), legacy_gui, "Scorp96",
                role_gui_turn=role_gui, v3_runtime=v3,
            )
            result = await runtime.run_once()
            self.assertEqual("ROUTED", result["status"])
            self.assertEqual(1, v3.calls)
            self.assertEqual(1, len(role_calls))

    async def test_production_v3_runtime_idles_until_valid_project_state_exists(self):
        runtime_path = Path(__file__).resolve().parents[1] / "production_v3_runtime.py"
        self.assertTrue(runtime_path.is_file())
        from production_v3_runtime import ProductionV3Runtime
        with tempfile.TemporaryDirectory() as td:
            runtime = ProductionV3Runtime(Path(td))
            result = await runtime.run_once()
            self.assertEqual("IDLE", result["status"])
            self.assertEqual("V3", result["runtime"])

    def test_installer_contains_complete_v3_runtime_manifest_and_cli_binding(self):
        installer = (Path(__file__).resolve().parents[1] / "install-bridge.ps1").read_text(encoding="utf-8")
        required = [
            "production_v3_runtime.py",
            "actor_gui_backend_v3.py",
            "actor_response_journal_v3.py",
            "chrome_use_cli_v3.py",
            "chrome_use_actor_driver_v3.py",
            "continuation_watchdog_v3.py",
            "durable_actor_transport_v3.py",
            "master_state_transition_v3.py",
            "master_window_lease_v3.py",
            "master_worker_coordinator_v3.py",
            "master_worker_relay_v3.py",
            "parallel_master_worker_relay_v3.py",
            "project_state_v3.py",
            "session_registry_v3.py",
            "turn_scheduler_v3.py",
            "windows_mcp_actor_driver_v3.py",
            "worker_event_pump_v3.py",
            "worker_event_queue_v3.py",
        ]
        for name in required:
            self.assertIn(name, installer)
        self.assertIn("--v3-project-root", installer)
        self.assertIn("C:\\ScorpAgent\\state-v3\\active", installer)


if __name__ == "__main__":
    unittest.main()
