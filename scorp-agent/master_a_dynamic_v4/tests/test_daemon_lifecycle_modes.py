from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.activation_arbiter import ArbiterSnapshot
from master_a_dynamic_v4.daemon import LocalDaemon
from master_a_dynamic_v4.runtime_commands import RuntimeCommandService
from master_a_dynamic_v4.runtime_protocol import parse_request
from master_a_dynamic_v4.state_store import StateStore


class DaemonLifecycleModeTests(unittest.TestCase):
    def _store(self, root: Path) -> StateStore:
        store = StateStore(root / "state.sqlite3", [root])
        store.create_contract(
            "p", root_contract={"objective": "x"}, acceptance_contract={"ids": []}
        )
        return store

    def test_observe_only_stays_alive_without_mutating_maintenance_callbacks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            calls: list[str] = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=1,
                execution_mode="OBSERVE_ONLY",
                snapshot_provider=lambda: ArbiterSnapshot(
                    "p", "ACTIVE", 0, 1, False, 0, 2, 0, 0
                ),
                worker_lease_recovery=lambda: calls.append("recover"),
                worker_lease_renewal=lambda: calls.append("renew"),
                project_completion=lambda: calls.append("complete"),
                action_handlers={"RESUME_MASTER": lambda _decision: calls.append("action")},
                health_path=root / "health.json",
            )

            decisions = daemon.run_loop(
                interval_seconds=0, max_iterations=3, sleep=lambda _value: None
            )

            self.assertEqual(3, len(decisions))
            self.assertEqual(["RESUME_MASTER"] * 3, [item.action for item in decisions])
            self.assertEqual([], calls)
            self.assertEqual(0, store.count_activation_decisions("p"))
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertEqual("HEALTHY", health["status"])
            self.assertEqual("OBSERVE_ONLY", health["execution_mode"])
            self.assertEqual("RUNNING", health["scheduling_state"])
            self.assertFalse(health["dispatch_allowed"])

    def test_observe_only_keeps_real_operator_blocker_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=1,
                execution_mode="OBSERVE_ONLY",
                snapshot_provider=lambda: ArbiterSnapshot(
                    "p",
                    "ACTIVE",
                    0,
                    1,
                    False,
                    0,
                    2,
                    0,
                    0,
                    auth_host_blocker="CHATGPT_LOGIN_REQUIRED",
                ),
                health_path=root / "health.json",
            )

            decisions = daemon.run_loop(
                interval_seconds=0, max_iterations=3, sleep=lambda _value: None
            )

            self.assertEqual(1, len(decisions))
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", health["status"])
            self.assertFalse(health["dispatch_allowed"])

    def test_paused_stays_alive_and_only_heartbeats_existing_master(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            with store._connection() as conn:
                conn.execute(
                    "UPDATE operator_controls SET operator_state='PAUSED' WHERE project_id='p'"
                )
            calls: list[str] = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=1,
                execution_mode="ACTIVE",
                snapshot_provider=lambda: ArbiterSnapshot(
                    "p", "ACTIVE", 0, 1, True, 0, 2, 0, 0, operator_state="PAUSED"
                ),
                paused_master_heartbeat=lambda: calls.append("master-heartbeat"),
                worker_lease_recovery=lambda: calls.append("recover"),
                worker_lease_renewal=lambda: calls.append("renew"),
                project_completion=lambda: calls.append("complete"),
                action_handlers={"BLOCKED": lambda _decision: calls.append("action")},
                health_path=root / "health.json",
            )

            decisions = daemon.run_loop(
                interval_seconds=0, max_iterations=2, sleep=lambda _value: None
            )

            self.assertEqual(2, len(decisions))
            self.assertEqual(["BLOCKED", "BLOCKED"], [item.action for item in decisions])
            self.assertEqual(["master-heartbeat", "master-heartbeat"], calls)
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertEqual("HEALTHY", health["status"])
            self.assertEqual("ACTIVE", health["execution_mode"])
            self.assertEqual("PAUSED", health["scheduling_state"])
            self.assertFalse(health["dispatch_allowed"])

    def test_observe_only_runtime_pipe_is_read_only_and_exposes_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = self._store(root)
            lease = store.acquire_daemon_lease("p", "daemon-1")
            service = RuntimeCommandService(
                store,
                project_id="p",
                daemon_epoch=lease["daemon_epoch"],
                execution_mode="OBSERVE_ONLY",
            )
            status_request = parse_request(
                {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "status-observe",
                    "command": "runtime.status",
                    "project_id": "p",
                    "payload": {},
                }
            )
            mutation_request = parse_request(
                {
                    "protocol_version": "scorp.runtime.command/1",
                    "request_id": "pause-observe",
                    "command": "project.pause",
                    "project_id": "p",
                    "expected_state_version": 0,
                    "expected_daemon_epoch": lease["daemon_epoch"],
                    "payload": {},
                }
            )

            status = service.execute(status_request)
            rejected = service.execute(mutation_request)

            self.assertEqual("OBSERVE_ONLY", status["result"]["execution_mode"])
            self.assertEqual("RUNNING", status["result"]["scheduling_state"])
            self.assertFalse(status["result"]["dispatch_allowed"])
            self.assertEqual("REJECTED", rejected["status"])
            self.assertEqual("OBSERVE_ONLY_MUTATION_FORBIDDEN", rejected["error"]["code"])
            self.assertEqual("RUNNING", store.get_operator_control("p")["operator_state"])


if __name__ == "__main__":
    unittest.main()
