import datetime as dt
import json
import pathlib
import tempfile
import unittest

from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from project_state_v3 import ProjectStateStore

UTC = dt.timezone.utc


class MasterStateTransitionV3Tests(unittest.TestCase):
    def _state(self):
        return {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": 7,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "PHASE_1",
            "next_exact_action": "old action",
            "active_master_window": None,
            "active_workers": [],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

    def _turn(self, kind="MASTER"):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "turn-7",
            "project_id": "p",
            "state_version": 7,
            "actor_kind": kind,
            "actor_id": "A" if kind == "MASTER" else "worker-1",
            "session_id": "a-window-7" if kind == "MASTER" else "worker-1-session",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {},
        }

    def _make(self, td):
        root = pathlib.Path(td)
        state_path = root / "state.json"
        state_path.write_text(json.dumps(self._state()), encoding="utf-8")
        store = ProjectStateStore(state_path)
        store.load()
        leases = MasterWindowLeaseStore(root / "lease.json")
        now = dt.datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
        leases.acquire("p", "a-window-7", "turn-7", 7, now=now, ttl_seconds=1500)
        return store, leases, MasterStateTransitionV3(store, leases), now

    def test_continue_cas_updates_state_and_heartbeats_same_master(self):
        with tempfile.TemporaryDirectory() as td:
            store, leases, tx, now = self._make(td)
            result = tx.apply(self._turn(), {
                "kind": "CONTINUE",
                "state_patch": {"current_phase": "PHASE_2", "next_exact_action": "run next tests"},
            }, now=now + dt.timedelta(minutes=2))
            self.assertEqual(8, result["state"]["state_version"])
            self.assertEqual("PHASE_2", result["state"]["current_phase"])
            self.assertEqual("run next tests", result["state"]["next_exact_action"])
            self.assertTrue(result["state"]["last_master_transition_id"].startswith("master-tx-"))
            self.assertEqual("ACTIVE", leases.load("p")["status"])

    def test_dispatch_records_dynamic_workers_in_authoritative_state(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, tx, now = self._make(td)
            workers = [
                {"worker_id": "worker-aaa", "session_id": "worker-aaa-session-1", "objective_sha256": "1" * 64},
                {"worker_id": "worker-bbb", "session_id": "worker-bbb-session-1", "objective_sha256": "2" * 64},
                {"worker_id": "worker-ccc", "session_id": "worker-ccc-session-1", "objective_sha256": "3" * 64},
            ]
            result = tx.apply(self._turn(), {
                "kind": "DISPATCH",
                "state_patch": {"next_exact_action": "continue core work"},
            }, allocated_workers=workers, now=now + dt.timedelta(minutes=1))
            self.assertEqual(8, result["state"]["state_version"])
            self.assertEqual(workers, result["state"]["active_workers"])

    def test_drain_persists_safe_handoff_before_releasing_window(self):
        with tempfile.TemporaryDirectory() as td:
            store, leases, tx, now = self._make(td)
            result = tx.apply(self._turn(), {
                "kind": "DRAIN",
                "state_patch": {
                    "current_phase": "PHASE_2",
                    "next_exact_action": "resume exact action",
                    "tests": ["24 files PASS"],
                },
            }, now=now + dt.timedelta(minutes=20))
            self.assertEqual(8, result["state"]["state_version"])
            self.assertEqual("ACTIVE", result["state"]["status"])
            self.assertEqual("resume exact action", result["state"]["next_exact_action"])
            self.assertEqual("DRAINED", leases.load("p")["status"])

    def test_terminal_only_allows_complete_or_hard_blocked_and_releases_lease(self):
        for terminal in ("COMPLETE", "HARD_BLOCKED"):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as td:
                store, leases, tx, now = self._make(td)
                result = tx.apply(self._turn(), {
                    "kind": "TERMINAL",
                    "terminal_status": terminal,
                    "state_patch": {"next_exact_action": "none" if terminal == "COMPLETE" else "await user decision"},
                }, now=now + dt.timedelta(minutes=3))
                self.assertEqual(terminal, result["state"]["status"])
                self.assertEqual("RELEASED", leases.load("p")["status"])

        with tempfile.TemporaryDirectory() as td:
            _, _, tx, now = self._make(td)
            with self.assertRaisesRegex(ValueError, "MASTER_TERMINAL_STATUS_INVALID"):
                tx.apply(self._turn(), {"kind": "TERMINAL", "terminal_status": "FAILED", "state_patch": {"next_exact_action": "x"}}, now=now)

    def test_same_transition_replay_is_idempotent_after_state_write(self):
        with tempfile.TemporaryDirectory() as td:
            store, leases, tx, now = self._make(td)
            response = {"kind": "DRAIN", "state_patch": {"next_exact_action": "resume safely"}}
            first = tx.apply(self._turn(), response, now=now + dt.timedelta(minutes=20))
            second = tx.apply(self._turn(), response, now=now + dt.timedelta(minutes=20))
            self.assertEqual(first["transition_id"], second["transition_id"])
            self.assertEqual(8, store.load()["state_version"])
            self.assertTrue(second["replayed"])
            self.assertEqual("DRAINED", leases.load("p")["status"])

    def test_replay_repairs_lease_after_crash_post_state_write(self):
        with tempfile.TemporaryDirectory() as td:
            store, leases, tx, now = self._make(td)
            response = {"kind": "DRAIN", "state_patch": {"next_exact_action": "resume after crash"}}
            real_drain = leases.drain

            def fail_after_state(*args, **kwargs):
                raise RuntimeError("SIMULATED_POST_STATE_CRASH")

            leases.drain = fail_after_state
            with self.assertRaisesRegex(RuntimeError, "SIMULATED_POST_STATE_CRASH"):
                tx.apply(self._turn(), response, now=now + dt.timedelta(minutes=20))
            self.assertEqual(8, store.load()["state_version"])
            self.assertEqual("ACTIVE", leases.load("p")["status"])

            leases.drain = real_drain
            replay = tx.apply(self._turn(), response, now=now + dt.timedelta(minutes=20))
            self.assertTrue(replay["replayed"])
            self.assertEqual(8, store.load()["state_version"])
            self.assertEqual("DRAINED", leases.load("p")["status"])

    def test_same_turn_with_changed_response_is_not_replay(self):
        with tempfile.TemporaryDirectory() as td:
            store, _, tx, now = self._make(td)
            tx.apply(self._turn(), {
                "kind": "CONTINUE",
                "state_patch": {"next_exact_action": "first action"},
            }, now=now + dt.timedelta(minutes=1))
            with self.assertRaisesRegex(ValueError, "MASTER_STATE_VERSION_CONFLICT"):
                tx.apply(self._turn(), {
                    "kind": "CONTINUE",
                    "state_patch": {"next_exact_action": "different action"},
                }, now=now + dt.timedelta(minutes=1))
            self.assertEqual(8, store.load()["state_version"])

    def test_stale_or_wrong_contract_turn_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, tx, now = self._make(td)
            turn = self._turn()
            turn["state_version"] = 6
            with self.assertRaisesRegex(ValueError, "MASTER_STATE_VERSION_CONFLICT"):
                tx.apply(turn, {"kind": "CONTINUE", "state_patch": {"next_exact_action": "x"}}, now=now)

        with tempfile.TemporaryDirectory() as td:
            _, _, tx, now = self._make(td)
            turn = self._turn()
            turn["root_objective_sha256"] = "x" * 64
            with self.assertRaisesRegex(ValueError, "MASTER_GOAL_CONTRACT_MISMATCH"):
                tx.apply(turn, {"kind": "CONTINUE", "state_patch": {"next_exact_action": "x"}}, now=now)

    def test_worker_cannot_mutate_project_state(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, tx, now = self._make(td)
            with self.assertRaisesRegex(ValueError, "PROJECT_STATE_MASTER_ONLY"):
                tx.apply(self._turn("WORKER"), {"kind": "HANDOFF", "state_patch": {"next_exact_action": "bad"}}, now=now)


if __name__ == "__main__":
    unittest.main()
