"""Machine-verifiable Worker result contract.

Natural-language Worker output is useful to Master A, but it is not sufficient
to advance an authoritative task.  This module validates the identity and
evidence envelope before the scheduler can admit a structured ``WORK_RESULT``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


WORK_RESULT_VERSION = "1"
WORK_RESULT_STATUSES = {"COMPLETE", "PARTIAL", "BLOCKED", "INVALID"}
_LIST_FIELDS = (
    "scope_completed",
    "scope_not_completed",
    "deliverables",
    "evidence",
    "acceptance_coverage",
    "facts",
    "inferences",
    "unknowns",
    "contradictions",
    "followup_proposals",
)


class WorkResultRejected(ValueError):
    """Raised when a Worker result cannot be bound to one assignment."""


def _require_hex(value: Any, *, name: str, lengths: tuple[int, ...]) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) not in lengths or any(char not in "0123456789abcdef" for char in normalized):
        raise WorkResultRejected(f"{name}_INVALID")
    return normalized


def _require_nonempty(value: Any, *, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise WorkResultRejected(f"{name}_MISSING")
    return normalized


def validate_work_result(
    value: Mapping[str, Any],
    *,
    project_id: str,
    assignment_id: str,
    task_id: str,
    objective_sha256: str,
    base_state_version: int,
) -> dict[str, Any]:
    """Validate and normalize one result against its claimed assignment.

    ``base_state_version`` is the project state version observed when the
    assignment was created.  A result from another graph revision is stale and
    must not be silently merged.
    """

    if not isinstance(value, Mapping):
        raise WorkResultRejected("WORK_RESULT_NOT_MAPPING")
    result = dict(value)
    if str(result.get("work_result_version") or "") != WORK_RESULT_VERSION:
        raise WorkResultRejected("WORK_RESULT_VERSION_UNSUPPORTED")

    expected_project = _require_nonempty(project_id, name="PROJECT_ID")
    expected_assignment = _require_nonempty(assignment_id, name="ASSIGNMENT_ID")
    expected_task = _require_nonempty(task_id, name="TASK_ID")
    expected_objective = _require_hex(
        objective_sha256, name="OBJECTIVE_SHA256", lengths=(64,)
    )
    actual_project = _require_nonempty(result.get("project_id"), name="PROJECT_ID")
    if actual_project != expected_project:
        raise WorkResultRejected("PROJECT_ID_MISMATCH")
    actual_assignment = _require_nonempty(result.get("assignment_id"), name="ASSIGNMENT_ID")
    if actual_assignment != expected_assignment:
        raise WorkResultRejected("ASSIGNMENT_ID_MISMATCH")
    actual_task = _require_nonempty(result.get("task_id"), name="TASK_ID")
    if actual_task != expected_task:
        raise WorkResultRejected("TASK_ID_MISMATCH")
    actual_objective = _require_hex(
        result.get("objective_sha256"), name="OBJECTIVE_SHA256", lengths=(64,)
    )
    if actual_objective != expected_objective:
        raise WorkResultRejected("OBJECTIVE_HASH_MISMATCH")

    observed_version = result.get("base_state_version")
    if isinstance(observed_version, bool):
        raise WorkResultRejected("BASE_STATE_VERSION_INVALID")
    try:
        observed_version = int(observed_version)
        expected_version = int(base_state_version)
    except (TypeError, ValueError):
        raise WorkResultRejected("BASE_STATE_VERSION_INVALID")
    if observed_version < 0 or observed_version != expected_version:
        raise WorkResultRejected("BASE_STATE_VERSION_MISMATCH")

    worker_id = _require_nonempty(result.get("worker_id"), name="WORKER_ID")
    candidate_commit = _require_hex(
        result.get("candidate_commit"), name="CANDIDATE_COMMIT", lengths=(40, 64)
    )
    status = str(result.get("status") or "").strip().upper()
    if status not in WORK_RESULT_STATUSES:
        raise WorkResultRejected("STATUS_INVALID")
    normalized: dict[str, Any] = dict(result)
    normalized.update(
        {
            "work_result_version": WORK_RESULT_VERSION,
            "project_id": actual_project,
            "assignment_id": actual_assignment,
            "task_id": actual_task,
            "objective_sha256": actual_objective,
            "base_state_version": observed_version,
            "worker_id": worker_id,
            "candidate_commit": candidate_commit,
            "status": status,
            "result_sha256": _require_hex(
                result.get("result_sha256"), name="RESULT_SHA256", lengths=(64,)
            ),
        }
    )
    for field in _LIST_FIELDS:
        value_for_field = result.get(field)
        if not isinstance(value_for_field, Sequence) or isinstance(value_for_field, (str, bytes)):
            raise WorkResultRejected(f"{field.upper()}_INVALID")
        normalized[field] = list(value_for_field)
    if status == "COMPLETE":
        if not normalized["evidence"]:
            raise WorkResultRejected("COMPLETE_EVIDENCE_MISSING")
        if not normalized["acceptance_coverage"]:
            raise WorkResultRejected("COMPLETE_ACCEPTANCE_COVERAGE_MISSING")
    return normalized
