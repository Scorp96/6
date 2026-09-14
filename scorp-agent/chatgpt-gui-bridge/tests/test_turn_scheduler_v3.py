import datetime as dt
import json
import pathlib
import tempfile
import unittest

from master_window_lease_v3 import MasterWindowLeaseStore
from project_state_v3 import ProjectStateStore
from turn_scheduler_v3 import TurnSchedulerV3

UTC = dt.timezone.utc


def master(turn_id, version, session, event="CONTINUE_CORE"):
    return {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": turn_id,
        "project_id": "p",
        "state_version": version,
        "actor_kind": "MASTER",
        "actor_id": "A",
        "session_id": session,
        "root_objective_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "payload": {"event": event},
    }


def worker(turn_id, version, worker_id):
    return {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": turn_id,
        "project_id": "p",
        "state_version": version,
        "actor_kind": "WORKER",
        "actor_id": worker_id,
        "session_id": worker_id + "-session",
        "root_objective_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "objective_sha256": "1" * 64,
        "payload": {"event": "ASSIGNMENT"},
    }


class TurnSchedulerV3Tests(unittest.TestCase):
    def _make(self, td, version=5, active_workers=None, lease=True):
        root = pathlib.Path(td)
        state = {
            "protocol_version": "scorp.project-state/v1",
            "project_id": "p",
            "master_identity": "A",
            "state_version": version,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "current_phase": "RUN",
            "next_exact_action": "continue",
            "active_master_window": None,
            "active_workers": active_workers or [],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }
        state_path = root / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        store = ProjectStateStore(state_path)
        leases = MasterWindowLeaseStore(root / "lease.json")
        now = dt.datetime(2026, 9, 11, 10, 0, tzinfo=UTC)
        if lease:
            leases.acquire("p", "a-current", "resume", version, now=now, ttl_seconds=3600)
        return TurnSchedulerV3(store, leases), now

    def _candidate(self, turn):
        return {"turn_id": turn["turn_id"], "digest": turn["turn_id"] + "-sha", "turn": turn}

    def test_resume_master_has_priority_over_workers_after_new_window_activation(self):
        with tempfile.TemporaryDirectory() as td:
            active = [{"worker_id": "worker-one", "session_id": "worker-one-session", "objective_sha256": "1" * 64}]
            scheduler, now = self._make(td, active_workers=active)
            candidates = [
                self._candidate(worker("worker-turn", 4, "worker-one")),
                self._candidate(master("resume-turn", 5, "a-current", "RESUME_MASTER")),
            ]
            result = scheduler.select(candidates, {}, now=now)
            self.assertEqual("resume-turn", result["selected"]["turn_id"])
            self.assertEqual([], result["superseded"])

    def test_worker_event_master_has_priority_over_normal_core_turn(self):
        with tempfile.TemporaryDirectory() as td:
            scheduler, now = self._make(td)
            event_turn = master("event-turn", 5, "a-current", "WORKER_HANDOFF")
            event_turn["payload"]["worker_event_id"] = "event-1"
            candidates = [
                self._candidate(master("core-turn", 5, "a-current")),
                self._candidate(event_turn),
            ]
            result = scheduler.select(candidates, {}, now=now)
            self.assertEqual("event-turn", result["selected"]["turn_id"])

    def test_normal_master_and_worker_alternate_using_last_routed_actor(self):
        with tempfile.TemporaryDirectory() as td:
            active = [{"worker_id": "worker-one", "session_id": "worker-one-session", "objective_sha256": "1" * 64}]
            scheduler, now = self._make(td, active_workers=active)
            candidates = [
                self._candidate(master("core-turn", 5, "a-current")),
                self._candidate(worker("worker-turn", 4, "worker-one")),
            ]
            after_master = {
                "old-master": {"state": "ROUTED", "actor_kind": "MASTER", "routed_at": "2026-09-11T09:00:00Z"}
            }
            after_worker = {
                "old-worker": {"state": "ROUTED", "actor_kind": "WORKER", "routed_at": "2026-09-11T09:00:00Z"}
            }
            self.assertEqual("worker-turn", scheduler.select(candidates, after_master, now=now)["selected"]["turn_id"])
            self.assertEqual("core-turn", scheduler.select(candidates, after_worker, now=now)["selected"]["turn_id"])

    def test_stale_or_wrong_session_master_is_superseded_but_older_active_worker_remains_runnable(self):
        with tempfile.TemporaryDirectory() as td:
            active = [{"worker_id": "worker-one", "session_id": "worker-one-session", "objective_sha256": "1" * 64}]
            scheduler, now = self._make(td, version=5, active_workers=active)
            candidates = [
                self._candidate(master("stale-version", 4, "a-current")),
                self._candidate(master("stale-session", 5, "a-old")),
                self._candidate(worker("worker-turn", 3, "worker-one")),
            ]
            result = scheduler.select(candidates, {}, now=now)
            self.assertEqual("worker-turn", result["selected"]["turn_id"])
            reasons = {row["turn_id"]: row["reason"] for row in result["superseded"]}
            self.assertEqual("MASTER_STATE_VERSION_STALE", reasons["stale-version"])
            self.assertEqual("MASTER_SESSION_STALE", reasons["stale-session"])

    def test_inactive_worker_turn_is_superseded(self):
        with tempfile.TemporaryDirectory() as td:
            scheduler, now = self._make(td, active_workers=[])
            result = scheduler.select([self._candidate(worker("orphan-worker", 5, "worker-gone"))], {}, now=now)
            self.assertIsNone(result["selected"])
            self.assertEqual("WORKER_NOT_ACTIVE", result["superseded"][0]["reason"])

    def test_terminal_project_selects_nothing_and_supersedes_all(self):
        with tempfile.TemporaryDirectory() as td:
            scheduler, now = self._make(td)
            state = scheduler.project_state_store.load()
            state["status"] = "COMPLETE"
            pathlib.Path(scheduler.project_state_store.path).write_text(json.dumps(state), encoding="utf-8")
            candidates = [self._candidate(master("core-turn", 5, "a-current"))]
            result = scheduler.select(candidates, {}, now=now)
            self.assertIsNone(result["selected"])
            self.assertEqual("PROJECT_TERMINAL", result["superseded"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
