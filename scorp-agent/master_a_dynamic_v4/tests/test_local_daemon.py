from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from master_a_dynamic_v4.activation_arbiter import ArbiterSnapshot
from master_a_dynamic_v4.daemon import LocalDaemon
from master_a_dynamic_v4.state_store import StateStore


class LocalDaemonTests(unittest.TestCase):
    def test_run_once_persists_decision_and_separates_progress_from_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = StateStore(root / "state.sqlite3", [root])
            store.create_contract("p", root_contract={"objective": "x"}, acceptance_contract={"ids": []})
            observed = []
            daemon = LocalDaemon(
                store,
                project_id="p",
                daemon_epoch=3,
                snapshot_provider=lambda: ArbiterSnapshot("p", "ACTIVE", 0, 3, True, 0, 1, 1, 0),
                action_handlers={"ASSIGN_WORKER": lambda decision: observed.append(decision.action)},
                health_path=root / "health.json",
            )
            result = daemon.run_once()
            self.assertEqual("ASSIGN_WORKER", result.action)
            self.assertEqual(["ASSIGN_WORKER"], observed)
            self.assertEqual(1, store.count_activation_decisions("p"))
            health = json.loads((root / "health.json").read_text(encoding="utf-8"))
            self.assertEqual("HEALTHY", health["status"])
            self.assertEqual("IDLE", health["liveness"]["state"])
            self.assertEqual("ASSIGN_WORKER", health["last_decision"]["action"])
            self.assertTrue(health["heartbeat_at"])

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


if __name__ == "__main__":
    unittest.main()
