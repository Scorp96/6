from __future__ import annotations

import json


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _scope(value, error: str) -> list[str]:
    raw = value or []
    if not isinstance(raw, list):
        raise ValueError(error)
    found = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            raise ValueError(error)
        found.add(text)
    return sorted(found)


class WorkerResultGuardV3:
    """Pure deterministic alignment checks for worker evidence.

    A worker result never becomes authoritative through this guard. The guard
    only classifies whether the result is structurally aligned with the current
    project/assignment so persistent Master A can review it safely.
    """

    def evaluate(self, state: dict, event: dict) -> dict:
        if not isinstance(state, dict):
            raise ValueError("WORKER_GUARD_STATE_INVALID")
        if not isinstance(event, dict):
            raise ValueError("WORKER_GUARD_EVENT_INVALID")

        worker_id = str(event.get("worker_id") or "").strip()
        objective = str(event.get("objective_sha256") or "").strip()
        base = {
            "authoritative": False,
            "worker_id": worker_id,
            "objective_sha256": objective,
        }

        mismatches = []
        if event.get("project_id") != state.get("project_id"):
            mismatches.append("project_id")
        if event.get("root_objective_sha256") != state.get("goal_contract_sha256"):
            mismatches.append("root_objective_sha256")
        if event.get("acceptance_sha256") != state.get("acceptance_sha256"):
            mismatches.append("acceptance_sha256")
        if mismatches:
            return {**base, "verdict": "HASH_MISMATCH", "mismatches": mismatches}

        try:
            source_version = int(event.get("source_state_version"))
            state_version = int(state.get("state_version"))
        except Exception as exc:
            raise ValueError("WORKER_GUARD_STATE_VERSION_INVALID") from exc
        if source_version < 0 or source_version > state_version:
            return {
                **base,
                "verdict": "STALE",
                "source_state_version": source_version,
                "current_state_version": state_version,
            }

        active_workers = state.get("active_workers") or []
        if not isinstance(active_workers, list):
            raise ValueError("WORKER_GUARD_ACTIVE_WORKERS_INVALID")
        matches = [row for row in active_workers if isinstance(row, dict) and row.get("worker_id") == worker_id]
        if len(matches) != 1:
            return {**base, "verdict": "STALE", "active_worker_matches": len(matches)}
        active = matches[0]

        if active.get("objective_sha256") != objective:
            return {**base, "verdict": "HASH_MISMATCH", "mismatches": ["objective_sha256"]}

        expected_assignment = active.get("assignment") or {}
        event_assignment = event.get("assignment") or {}
        if not isinstance(expected_assignment, dict) or not isinstance(event_assignment, dict):
            raise ValueError("WORKER_GUARD_ASSIGNMENT_INVALID")
        if _canonical(expected_assignment) != _canonical(event_assignment):
            return {**base, "verdict": "ASSIGNMENT_MISMATCH"}

        allowed_scope = _scope(expected_assignment.get("resource_scope"), "WORKER_GUARD_ASSIGNMENT_SCOPE_INVALID")
        worker_response = event.get("worker_response") or {}
        if not isinstance(worker_response, dict):
            raise ValueError("WORKER_GUARD_RESPONSE_INVALID")
        claimed_scope = _scope(worker_response.get("resource_scope"), "WORKER_GUARD_RESPONSE_SCOPE_INVALID")
        out_of_scope = sorted(set(claimed_scope).difference(allowed_scope)) if claimed_scope else []
        if out_of_scope:
            return {
                **base,
                "verdict": "SCOPE_VIOLATION",
                "assignment_resource_scope": allowed_scope,
                "claimed_resource_scope": claimed_scope,
                "out_of_scope": out_of_scope,
            }

        return {
            **base,
            "verdict": "ALIGNED",
            "source_state_version": source_version,
            "current_state_version": state_version,
            "assignment_resource_scope": allowed_scope,
            "claimed_resource_scope": claimed_scope,
        }
