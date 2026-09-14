import datetime as dt
import json
import pathlib
import tempfile
import unittest

from continuation_watchdog_v3 import ContinuationWatchdog
from master_window_lease_v3 import MasterWindowLeaseStore
from project_state_v3 import ProjectStateStore


UTC = dt.timezone.utc


class ContinuationWatchdogV3Tests(unittest.TestCase):
    def _state(self, status="ACTIVE", active_master_window=None):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "social-cbi-production",
            "master_identity": "A",
            "state_version": 7,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": status,
            "current_phase": "LIVE_BROWSER_ACCEPTANCE",
            "next_exact_action": "run guarded live canary",
            "active_master_window": active_master_window,
            "active_workers": [],
            "completed": ["social-core"],
            "blocked": [],
            "evidence": ["evidence-1"],
            "commits": ["abc123"],
            "tests": ["31/31 PASS"],
        }

    def _make(self, td, state):
        root = pathlib.Path(td)
        state_path = root / "project-state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        store = ProjectStateStore(state_path)
        leases = MasterWindowLeaseStore(root / "master-window-lease.json")
        watchdog = ContinuationWatchdog(store, leases, root / "master-worker-v3-outbox")
        return root, leases, watchdog

    def test_active_project_without_master_emits_one_deterministic_v3_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, watchdog = self._make(td, self._state())
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            first = watchdog.run_once(now=now)
            second = watchdog.run_once(now=now + dt.timedelta(seconds=10))
            self.assertEqual("QUEUED", first["status"])
            self.assertEqual("ALREADY_QUEUED", second["status"])
            self.assertEqual(first["turn_id"], second["turn_id"])
            self.assertEqual(first["session_id"], second["session_id"])
            files = list((root / "master-worker-v3-outbox").glob("*.json"))
            self.assertEqual(1, len(files))
            turn = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual("scorp.master-worker/turn-v3", turn["protocol_version"])
            self.assertEqual("MASTER", turn["actor_kind"])
            self.assertEqual("A", turn["actor_id"])
            self.assertEqual(first["session_id"], turn["session_id"])
            self.assertEqual("RESUME_MASTER", turn["payload"]["event"])
            self.assertEqual(7, turn["state_version"])
            self.assertEqual("run guarded live canary", turn["payload"]["project_state"]["next_exact_action"])
            self.assertNotIn("assignments", turn["payload"])

    def test_live_operational_lease_prevents_resume_even_if_project_state_field_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            _, leases, watchdog = self._make(td, self._state(active_master_window=None))
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            leases.acquire("social-cbi-production", "a-window-live", "turn-live", 7, now=now, ttl_seconds=1200)
            result = watchdog.run_once(now=now + dt.timedelta(minutes=1))
            self.assertEqual("MASTER_ACTIVE", result["status"])

    def test_stale_project_state_window_field_is_not_authoritative(self):
        with tempfile.TemporaryDirectory() as td:
            stale = {"session_id": "old-chat", "lease_until": "2099-01-01T00:00:00Z"}
            _, _, watchdog = self._make(td, self._state(active_master_window=stale))
            result = watchdog.run_once(now=dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
            self.assertEqual("QUEUED", result["status"])

    def test_expired_operational_lease_allows_new_master_session(self):
        with tempfile.TemporaryDirectory() as td:
            _, leases, watchdog = self._make(td, self._state())
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            leases.acquire("social-cbi-production", "a-window-old", "turn-old", 7, now=now, ttl_seconds=60)
            result = watchdog.run_once(now=now + dt.timedelta(seconds=61))
            self.assertEqual("QUEUED", result["status"])
            self.assertEqual("LEASE_EXPIRED", result["reason"])
            self.assertNotEqual("a-window-old", result["session_id"])

    def test_complete_and_hard_blocked_never_resume(self):
        for status in ("COMPLETE", "HARD_BLOCKED"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root, _, watchdog = self._make(td, self._state(status=status))
                result = watchdog.run_once(now=dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC))
                self.assertEqual(status, result["status"])
                self.assertFalse((root / "master-worker-v3-outbox").exists())

    def test_state_version_changes_create_new_resume_and_session_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root, _, watchdog = self._make(td, self._state())
            now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
            first = watchdog.run_once(now=now)
            state_path = root / "project-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["state_version"] = 8
            state["next_exact_action"] = "integrate worker handoff"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            second = watchdog.run_once(now=now + dt.timedelta(minutes=1))
            self.assertNotEqual(first["turn_id"], second["turn_id"])
            self.assertNotEqual(first["session_id"], second["session_id"])
            self.assertEqual(2, len(list((root / "master-worker-v3-outbox").glob("*.json"))))


if __name__ == "__main__":
    unittest.main()
