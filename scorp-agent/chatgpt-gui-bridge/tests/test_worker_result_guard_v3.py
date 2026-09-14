import copy
import unittest

from worker_result_guard_v3 import WorkerResultGuardV3


class WorkerResultGuardV3Tests(unittest.TestCase):
    def _assignment(self):
        return {
            "objective_sha256": "1" * 64,
            "task": "edit bounded files",
            "resource_scope": ["file:a.py", "file:test_a.py"],
        }

    def _state(self):
        return {
            "project_id": "p",
            "state_version": 4,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "status": "ACTIVE",
            "active_workers": [{
                "worker_id": "worker-001",
                "session_id": "worker-001-session",
                "objective_sha256": "1" * 64,
                "assignment": self._assignment(),
            }],
        }

    def _event(self):
        return {
            "project_id": "p",
            "source_state_version": 3,
            "worker_id": "worker-001",
            "objective_sha256": "1" * 64,
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "assignment": self._assignment(),
            "worker_response": {
                "kind": "HANDOFF",
                "evidence": ["tests passed"],
                "resource_scope": ["file:a.py"],
            },
        }

    def test_aligned_event_is_accepted_as_evidence_not_authority(self):
        verdict = WorkerResultGuardV3().evaluate(self._state(), self._event())
        self.assertEqual("ALIGNED", verdict["verdict"])
        self.assertEqual("worker-001", verdict["worker_id"])
        self.assertEqual(["file:a.py", "file:test_a.py"], verdict["assignment_resource_scope"])
        self.assertFalse(verdict["authoritative"])

    def test_wrong_contract_hash_fails_guard(self):
        event = self._event()
        event["root_objective_sha256"] = "x" * 64
        verdict = WorkerResultGuardV3().evaluate(self._state(), event)
        self.assertEqual("HASH_MISMATCH", verdict["verdict"])
        self.assertIn("root_objective_sha256", verdict["mismatches"])

    def test_worker_missing_from_active_state_is_stale(self):
        state = self._state()
        state["active_workers"] = []
        verdict = WorkerResultGuardV3().evaluate(state, self._event())
        self.assertEqual("STALE", verdict["verdict"])

    def test_worker_claim_outside_assignment_scope_is_scope_violation(self):
        event = self._event()
        event["worker_response"]["resource_scope"] = ["file:a.py", "file:forbidden.py"]
        verdict = WorkerResultGuardV3().evaluate(self._state(), event)
        self.assertEqual("SCOPE_VIOLATION", verdict["verdict"])
        self.assertEqual(["file:forbidden.py"], verdict["out_of_scope"])

    def test_assignment_binding_change_is_rejected(self):
        event = self._event()
        event["assignment"] = copy.deepcopy(event["assignment"])
        event["assignment"]["task"] = "different task"
        verdict = WorkerResultGuardV3().evaluate(self._state(), event)
        self.assertEqual("ASSIGNMENT_MISMATCH", verdict["verdict"])


if __name__ == "__main__":
    unittest.main()
