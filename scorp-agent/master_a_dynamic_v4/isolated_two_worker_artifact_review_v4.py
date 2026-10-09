"""Read-only two-worker RESULT-ARTIFACT review barrier for isolated R2.

This reads two previously staged GitHub issue comments, one for each
pre-authorized worker assignment. It does NOT observe ChatGPT response final,
provide a host event, wake GPT, send a browser message, or dispatch work.

A review barrier cannot be used to bypass the existing host terminal issuer:
GitHub author IDs and exact artifact hashes establish only metadata
consistency of externally submitted work artifacts, not the author model,
its browser session, or successful completion of that ChatGPT turn.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3

from .isolated_github_work_result_inbox_v4 import _ID, _SHA, _safe_db


@dataclass(frozen=True)
class TwoWorkerReviewBarrier:
    status: str
    reason: str
    review_artifacts_present: int = 0
    both_artifacts_staged_for_human_review: bool = False
    real_worker_identity_attested: bool = False
    host_terminal_event_attested: bool = False
    browser_send_authorized: bool = False
    wake_master_authorized: bool = False
    wake_worker_authorized: bool = False
    local_execution_authorized: bool = False


def inspect_two_worker_review_only(
    *,
    database: Path,
    allowed_experiments_root: Path,
    expected_project_id: str,
    expected_state_version: int,
    worker_1_assignment_id: str,
    worker_1_assignment_sha256: str,
    worker_1_issue_number: int,
    worker_1_author_id: int,
    worker_2_assignment_id: str,
    worker_2_assignment_sha256: str,
    worker_2_issue_number: int,
    worker_2_author_id: int,
) -> TwoWorkerReviewBarrier:
    """Strict read-only. Exactly the two expected rows; no dispatch rights."""
    def result(status, reason, count=0, ready=False):
        return TwoWorkerReviewBarrier(
            status, reason, count, both_artifacts_staged_for_human_review=ready,
        )
    if (
        not _safe_db(database, allowed_experiments_root)
        or not database.is_file()
    ):
        return result("BLOCKED", "ISOLATED_WORK_REVIEW_LEDGER_REQUIRED")
    if (
        not isinstance(expected_project_id, str) or not _ID.fullmatch(expected_project_id)
        or type(expected_state_version) is not int or expected_state_version < 0
        or worker_1_assignment_id == worker_2_assignment_id
    ):
        return result("BLOCKED", "EXPECTED_TWO_WORKER_SCOPE_INVALID")
    for assignment, sha, issue, author in (
        (worker_1_assignment_id, worker_1_assignment_sha256,
         worker_1_issue_number, worker_1_author_id),
        (worker_2_assignment_id, worker_2_assignment_sha256,
         worker_2_issue_number, worker_2_author_id),
    ):
        if (
            not isinstance(assignment, str) or not _ID.fullmatch(assignment)
            or not isinstance(sha, str) or not _SHA.fullmatch(sha)
            or type(issue) is not int or issue < 1
            or type(author) is not int or author < 1
        ):
            return result("BLOCKED", "EXPECTED_TWO_WORKER_SCOPE_INVALID")
    project_digest = hashlib.sha256(
        expected_project_id.encode("ascii")
    ).hexdigest()
    expected = {}
    for slot, assignment, sha, issue, author in (
        ("worker-slot-1", worker_1_assignment_id,
         worker_1_assignment_sha256, worker_1_issue_number, worker_1_author_id),
        ("worker-slot-2", worker_2_assignment_id,
         worker_2_assignment_sha256, worker_2_issue_number, worker_2_author_id),
    ):
        key = hashlib.sha256(
            (expected_project_id + ":" + assignment).encode("ascii")
        ).hexdigest()
        expected[key] = (slot, sha, issue, author)
    try:
        with contextlib.closing(sqlite3.connect(
            database.resolve().as_uri() + "?mode=ro",
            uri=True, timeout=3,
        )) as db:
            db.execute("PRAGMA query_only=ON")
            # No schema repairs, writes, or unsanctioned migrations.
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                return result("BLOCKED", "WORK_REVIEW_LEDGER_INTEGRITY_FAILED")
            found = db.execute(
                "SELECT assignment_digest,project_digest,"
                "assignment_sha256,expected_state_version,"
                "expected_issue_number,trusted_author_id,worker_slot,"
                "artifact_sha256,status "
                "FROM r2_github_review_artifacts "
                "WHERE assignment_digest IN (?,?)",
                tuple(expected),
            ).fetchall()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return result("BLOCKED", "WORK_REVIEW_LEDGER_UNREADABLE")

    seen = set()
    for row in found:
        (assignment, project, sha, version, issue,
         author, slot, artifact, state) = row
        requirement = expected.get(assignment)
        if (
            requirement is None or assignment in seen
            or project != project_digest
            or type(version) is not int or version != expected_state_version
            or (slot, sha, issue, author) != requirement
            or not isinstance(artifact, str) or _SHA.fullmatch(artifact) is None
            or state != "STAGED_FOR_REVIEW_ONLY"
        ):
            return result("BLOCKED", "REVIEW_WORKER_SCOPE_OR_EVIDENCE_MISMATCH")
        seen.add(assignment)
    if len(seen) == 0:
        return result("AWAITING_ARTIFACTS", "NO_WORKER_ARTIFACTS_STAGED")
    if len(seen) == 1:
        return result(
            "AWAITING_SECOND_WORKER",
            "ONE_WORK_ARTIFACT_NOT_GPT_TERMINAL_EVENT", 1,
        )
    if len(seen) == 2:
        return result(
            "BOTH_ARTIFACTS_FOR_REVIEW",
            "TWO_SCOPED_ARTIFACTS_NOT_GPT_TERMINAL_EVENTS", 2, True,
        )
    return result("BLOCKED", "WORKER_REVIEW_CARDINALITY_INVALID")
