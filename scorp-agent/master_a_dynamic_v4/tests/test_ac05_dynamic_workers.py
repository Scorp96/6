from __future__ import annotations

import datetime as dt
import pathlib
import tempfile
import unittest


UTC = dt.timezone.utc


class DynamicWorkerTests(unittest.TestCase):
    def make_runtime(self, root: pathlib.Path):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore

        worktree = root / "worktree"
        lab = root / "lab"
        worktree.mkdir()
        lab.mkdir()
        store = StateStore(root / "state.sqlite3", allowed_roots=[worktree, lab])
        store.create_contract(
            "project-ac05",
            root_contract={"objective": "two dynamic Workers"},
            acceptance_contract={"required": ["AC05"]},
        )
        scheduler = Scheduler(
            store,
            "project-ac05",
            PathPolicy([worktree, lab]),
            max_workers=2,
        )
        return store, scheduler, worktree

    def test_scheduler_rejects_limit_above_configured_capacity(self):
        from master_a_dynamic_v4.scheduler import SchedulerError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler, worktree = self.make_runtime(root)
            try:
                scheduler.enqueue_graph([
                    {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "one.txt"], "dependencies": []}
                ])
                with self.assertRaisesRegex(SchedulerError, "V4_WORKER_LIMIT_INVALID"):
                    scheduler.claim_runnable(master_epoch=0, limit=3)
                self.assertEqual("QUEUED", scheduler.get_task("T1")["state"])
            finally:
                store.close()

    def test_scheduler_rejects_cyclic_task_graph_before_persisting_partial_graph(self):
        from master_a_dynamic_v4.scheduler import SchedulerError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler, worktree = self.make_runtime(root)
            try:
                with self.assertRaisesRegex(SchedulerError, "TASK_DEPENDENCY_CYCLE"):
                    scheduler.enqueue_graph(
                        [
                            {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "one.txt"], "dependencies": ["T2"]},
                            {"task_id": "T2", "objective_sha256": "2" * 64, "resource_scope": [worktree / "two.txt"], "dependencies": ["T1"]},
                        ]
                    )
                self.assertEqual("ACTIVE", scheduler.store.get_project_state("project-ac05")["status"])
                with store._connection() as conn:
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM task_nodes WHERE project_id=?", ("project-ac05",)).fetchone()[0])
            finally:
                store.close()

    def test_two_slots_queue_extra_work_then_release_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler, worktree = self.make_runtime(root)
            try:
                scheduler.enqueue_graph(
                    [
                        {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "t1.csv"], "dependencies": []},
                        {"task_id": "T2", "objective_sha256": "2" * 64, "resource_scope": [worktree / "t2.json"], "dependencies": []},
                        {"task_id": "T3", "objective_sha256": "3" * 64, "resource_scope": [worktree / "report.json"], "dependencies": ["T1", "T2"]},
                        {"task_id": "T4", "objective_sha256": "4" * 64, "resource_scope": [worktree / "extra.txt"], "dependencies": []},
                    ]
                )
                started = dt.datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
                first = scheduler.claim_runnable(master_epoch=0, now=started)
                self.assertEqual(["T1", "T2"], [claim.task_id for claim in first])
                self.assertEqual({"worker-slot-1", "worker-slot-2"}, {claim.slot_id for claim in first})
                self.assertEqual(2, len({claim.assignment_id for claim in first}))
                self.assertEqual(2, len({claim.worker_id for claim in first}))
                self.assertEqual(2, len({claim.lease_token for claim in first}))
                self.assertEqual([], scheduler.claim_runnable(master_epoch=0, now=started))
                self.assertEqual("QUEUED", scheduler.get_task("T4")["state"])
                self.assertEqual("QUEUED", scheduler.get_task("T3")["state"])

                r1 = scheduler.record_candidate(
                    first[0].assignment_id,
                    lease_token=first[0].lease_token,
                    master_epoch=0,
                    kind="HANDOFF",
                    payload={"artifact_sha256": "a" * 64, "result_sha256": "a" * 64},
                    now=started,
                )
                scheduler.verify_candidate(r1, result_sha256="a" * 64, now=started)
                refill = scheduler.claim_runnable(master_epoch=0, now=started)
                self.assertEqual(["T4"], [claim.task_id for claim in refill])
                self.assertEqual(first[0].slot_id, refill[0].slot_id)

                r2 = scheduler.record_candidate(first[1].assignment_id, lease_token=first[1].lease_token, master_epoch=0, kind="HANDOFF", payload={"artifact_sha256": "b" * 64, "result_sha256": "b" * 64}, now=started)
                r4 = scheduler.record_candidate(refill[0].assignment_id, lease_token=refill[0].lease_token, master_epoch=0, kind="HANDOFF", payload={"artifact_sha256": "d" * 64, "result_sha256": "d" * 64}, now=started)
                scheduler.verify_candidate(r2, result_sha256="b" * 64, now=started)
                scheduler.verify_candidate(r4, result_sha256="d" * 64, now=started)
                dependent = scheduler.claim_runnable(master_epoch=0, now=started)
                self.assertEqual(["T3"], [claim.task_id for claim in dependent])
            finally:
                store.close()

    def test_ambiguous_browser_intent_blocks_new_ordinary_claims(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler, worktree = self.make_runtime(root)
            try:
                scheduler.enqueue_graph([
                    {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "t1.txt"], "dependencies": []},
                    {"task_id": "T2", "objective_sha256": "2" * 64, "resource_scope": [worktree / "t2.txt"], "dependencies": []},
                ])
                started = dt.datetime(2026, 9, 15, 1, 0, tzinfo=UTC)
                first = scheduler.claim_runnable(master_epoch=0, now=started, limit=1)[0]
                store.prepare_intent(
                    "project-ac05", "ambiguous-intent", actor_id="worker-1",
                    channel="worker/1", action_kind="CHATGPT_SUBMIT",
                    payload={"assignment_id": first.assignment_id},
                )
                store.begin_possible_submit("ambiguous-intent")

                self.assertEqual([], scheduler.claim_runnable(master_epoch=0, now=started, limit=1))
                self.assertEqual("QUEUED", scheduler.get_task("T2")["state"])
            finally:
                store.close()

    def test_wrong_lease_old_epoch_and_expired_lease_are_fenced(self):
        from master_a_dynamic_v4.scheduler import WorkerFenceError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler, worktree = self.make_runtime(root)
            try:
                scheduler.enqueue_graph([
                    {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "one.txt"], "dependencies": []}
                ])
                started = dt.datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
                claim = scheduler.claim_runnable(master_epoch=0, now=started, lease_seconds=30)[0]
                with self.assertRaises(WorkerFenceError):
                    scheduler.record_candidate(claim.assignment_id, lease_token="wrong", master_epoch=0, kind="HANDOFF", payload={"x": 1}, now=started)
                store.advance_master_epoch("project-ac05", expected_epoch=0)
                with self.assertRaises(WorkerFenceError):
                    scheduler.record_candidate(claim.assignment_id, lease_token=claim.lease_token, master_epoch=0, kind="HANDOFF", payload={"x": 1}, now=started)
                scheduler.recover_expired_leases(now=started + dt.timedelta(seconds=31))
                self.assertEqual("FENCED", scheduler.get_assignment(claim.assignment_id)["state"])
                self.assertEqual("QUEUED", scheduler.get_task("T1")["state"])
            finally:
                store.close()


    def test_verify_candidate_rejects_digest_not_bound_to_recorded_result(self):
        from master_a_dynamic_v4.scheduler import SchedulerError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler, worktree = self.make_runtime(root)
            try:
                scheduler.enqueue_graph([
                    {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "one.txt"], "dependencies": []}
                ])
                started = dt.datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
                claim = scheduler.claim_runnable(master_epoch=0, now=started)[0]
                result_id = scheduler.record_candidate(
                    claim.assignment_id,
                    lease_token=claim.lease_token,
                    master_epoch=0,
                    kind="HANDOFF",
                    payload={"artifact_sha256": "a" * 64, "result_sha256": "a" * 64},
                    now=started,
                )
                with self.assertRaisesRegex(SchedulerError, "RESULT_SHA256_MISMATCH"):
                    scheduler.verify_candidate(result_id, result_sha256="f" * 64, now=started)
            finally:
                store.close()


    def test_verify_candidate_rejects_missing_canonical_result_identity(self):
        from master_a_dynamic_v4.scheduler import SchedulerError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store, scheduler, worktree = self.make_runtime(root)
            try:
                scheduler.enqueue_graph([
                    {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "one.txt"], "dependencies": []}
                ])
                started = dt.datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
                claim = scheduler.claim_runnable(master_epoch=0, now=started)[0]
                result_id = scheduler.record_candidate(
                    claim.assignment_id,
                    lease_token=claim.lease_token,
                    master_epoch=0,
                    kind="HANDOFF",
                    payload={"artifact_sha256": "a" * 64},
                    now=started,
                )
                with self.assertRaisesRegex(SchedulerError, "RESULT_IDENTITY_MISSING"):
                    scheduler.verify_candidate(result_id, result_sha256="a" * 64, now=started)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
