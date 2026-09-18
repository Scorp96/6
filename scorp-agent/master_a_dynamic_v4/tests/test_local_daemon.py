from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.activation_arbiter import ArbiterSnapshot
from master_a_dynamic_v4.daemon import LocalDaemon
from master_a_dynamic_v4.daemon import MasterSupervisorActionHandler
from master_a_dynamic_v4.state_store import StateStore


class LocalDaemonTests(unittest.TestCase):
    def test_resume_master_action_uses_existing_supervisor_without_browser_side_effects(self):
        class FakeSupervisor:
            def __init__(self):
                self.calls = 0

            def run_once(self):
                self.calls += 1
                return type("Decision", (), {"status": "MASTER_ACTIVE", "reason": "REBOUND"})()

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            supervisor = FakeSupervisor()
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, False, 0, 1, 0, 0),
                action_handlers={"RESUME_MASTER": MasterSupervisorActionHandler(supervisor)},
                health_path=root / "health.json",
            )
            self.assertEqual("RESUME_MASTER", daemon.run_once().action)
            self.assertEqual(1, supervisor.calls)
    def test_run_once_persists_decision_and_separates_progress_from_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            observed = []
            heartbeats = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                action_handlers={"ASSIGN_WORKER": lambda decision: observed.append(decision.action)},
                lease_heartbeat=lambda: heartbeats.append(True),
                health_path=root / "health.json",
            )
            result = daemon.run_once()
            self.assertEqual("ASSIGN_WORKER", result.action)
            self.assertEqual(["ASSIGN_WORKER"], observed)
            self.assertEqual([True, True], heartbeats)
            self.assertEqual(1, store.count_activation_decisions("p"))
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertEqual("HEALTHY", health["status"])
            self.assertEqual("IDLE", health["liveness"]["state"])
            self.assertEqual("ASSIGN_WORKER", health["last_decision"]["action"])
            self.assertTrue(health["heartbeat_at"])

    def test_worker_recovery_and_renewal_run_before_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            order = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                worker_lease_recovery=lambda: order.append("recover"),
                worker_lease_renewal=lambda: order.append("renew"),
                snapshot_provider=lambda: (order.append("snapshot") or ArbiterSnapshot("p", "ACTIVE", 0, 3, False, 0, 1, 0, 0)),
                health_path=root / "health.json",
            )
            daemon.run_once()
            self.assertEqual(["recover", "renew", "snapshot"], order)

    def test_failed_daemon_heartbeat_blocks_before_activation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                lease_heartbeat=lambda: (_ for _ in ()).throw(RuntimeError("LEASE_EXPIRED")),
                health_path=root / "health.json",
            )
            with self.assertRaisesRegex(RuntimeError, "LEASE_EXPIRED"):
                daemon.run_once()
            self.assertEqual("BLOCKED", json.loads((root / "health.json").read_text(encoding="utf-8"))["status"])

    def test_ambiguous_decision_has_no_default_action_handler(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, False, 0, 1, 1, 1),
                health_path=root / "health.json",
            )
            result = daemon.run_once()
            self.assertEqual("RECONCILE_AMBIGUOUS", result.action)
            self.assertEqual("BLOCKED", json.loads((root / "health.json").read_text(encoding="utf-8"))["status"])

    def test_lease_loss_before_action_fences_decision_and_handler(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            heartbeat_calls = []
            action_calls = []

            def heartbeat():
                heartbeat_calls.append(True)
                if len(heartbeat_calls) == 2:
                    raise RuntimeError("LEASE_FENCED_BEFORE_ACTION")

            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                action_handlers={"ASSIGN_WORKER": lambda _decision: action_calls.append(True)},
                lease_heartbeat=heartbeat,
                health_path=root / "health.json",
            )
            with self.assertRaisesRegex(RuntimeError, "LEASE_FENCED_BEFORE_ACTION"):
                daemon.run_once()
            self.assertEqual([True, True], heartbeat_calls)
            self.assertEqual([], action_calls)
            self.assertEqual(0, store.count_activation_decisions("p"))
            self.assertEqual("BLOCKED", json.loads((root / "health.json").read_text(encoding="utf-8"))["status"])

    def test_assignment_without_action_handler_is_blocked_not_reported_healthy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                health_path=root / "health.json",
            )
            result = daemon.run_once()
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertEqual("ASSIGN_WORKER", result.action)
            self.assertEqual("BLOCKED", health["status"])
            self.assertEqual("ACTION_HANDLER_REQUIRED:ASSIGN_WORKER", health["error"])

    def test_action_handler_blocked_result_is_not_reported_healthy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                action_handlers={"ASSIGN_WORKER": lambda _decision: {"status": "BLOCKED", "reason": "AUTH_REQUIRED"}},
                health_path=root / "health.json",
            )
            result = daemon.run_once()
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertEqual("ASSIGN_WORKER", result.action)
            self.assertEqual("BLOCKED", health["status"])
            self.assertEqual("ACTION_BLOCKED:AUTH_REQUIRED", health["error"])

    def test_run_loop_stops_after_blocked_action_without_retrying_next_iteration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            sleeps = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                action_handlers={"ASSIGN_WORKER": lambda _decision: {"status": "BLOCKED", "reason": "AUTH_REQUIRED"}},
                health_path=root / "health.json",
            )
            decisions = daemon.run_loop(
                interval_seconds=0,
                max_iterations=3,
                sleep=lambda value: sleeps.append(value),
            )
            self.assertEqual(1, len(decisions))
            self.assertEqual([], sleeps)

    def test_forever_style_loop_records_recovery_once_after_first_healthy_iteration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            recoveries = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 2, 0, 0),
                recovery_callback=lambda: recoveries.append("recovered"),
                health_path=root / "health.json",
            )
            decisions = daemon.run_loop(interval_seconds=0, max_iterations=3, sleep=lambda _value: None)
            self.assertEqual(3, len(decisions))
            self.assertEqual(["recovered"], recoveries)
            self.assertEqual("HEALTHY", json.loads((root / "health.json").read_text(encoding="utf-8"))["status"])

    def test_blocked_iteration_does_not_record_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            recoveries = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                recovery_callback=lambda: recoveries.append("recovered"),
                health_path=root / "health.json",
            )
            daemon.run_loop(interval_seconds=0, max_iterations=3, sleep=lambda _value: None)
            self.assertEqual([], recoveries)
            self.assertEqual("BLOCKED", json.loads((root / "health.json").read_text(encoding="utf-8"))["status"])

    def test_idle_updates_observation_but_not_progress_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 2, 0, 0),
                health_path=root / "health.json",
            )
            daemon.run_once()
            observation = store.get_runtime_observation("p")
            self.assertEqual("IDLE", observation["progress_state"])
            self.assertIsNone(observation["last_progress_at"])

    def test_active_generating_heartbeat_does_not_fabricate_progress(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot(
                    "p", "ACTIVE", 0, 3, True, 0, 0, 0, 0, progress_state="ACTIVE_GENERATING"
                ),
                health_path=root / "health.json",
            )
            daemon.run_once()
            observation = store.get_runtime_observation("p")
            self.assertEqual("ACTIVE_GENERATING", observation["progress_state"])
            self.assertIsNone(observation["last_progress_at"])
            self.assertTrue(observation["last_heartbeat_at"])
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertIsNone(health["liveness"]["last_progress_at"])


if __name__ == "__main__":
    unittest.main()
