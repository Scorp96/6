"""Experimental atomic comment + immutable blob + v2 work-text review intake.

Only an isolated experiments/r2-work-artifact-review-* SQLite is writable.
This component does not run GPT, touch Chrome, attest independent Workers,
alter production R1, wake Master/Workers, or authorize browser sending.

Existing legacy two-phase review rows are intentionally NOT migrated,
deleted or retried. A pre-existing orphan is reported for manual audit.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from .isolated_github_work_result_inbox_v4 import (
    _safe_db, _SCHEMA as _BASE_SCHEMA,
    _HEADER, _SHA, stage_explicit_github_worker_artifact_for_review,
)
from .isolated_gh_work_artifact_transport_v4 import _gh_get_comment
from .isolated_gh_immutable_artifact_v4 import (
    _PATH, _COMMIT, verify_immutable_github_artifact_for_review,
)
from .isolated_gh_verified_result_latch_v4 import (
    _SCHEMA as _BLOB_SCHEMA, _SUBSTANTIVE_SCHEMA,
)


@dataclass(frozen=True)
class AtomicResultForHumanReview:
    status: str
    reason: str
    comment_and_blob_verified: bool = False
    substantive_content_present: bool = False
    atomic_evidence_recorded: bool = False
    actual_worker_identity_verified: bool = False
    host_terminal_event_attested: bool = False
    browser_send_authorized: bool = False
    wake_master_authorized: bool = False
    wake_worker_authorized: bool = False
    local_execution_authorized: bool = False


def stage_atomic_pinned_substantive_artifact_for_review(
    *, database: Path, allowed_experiments_root: Path,
    comment_id: int, expected_issue_number: int,
    allowlisted_github_author_id: int,
    expected_project_id: str, expected_assignment_id: str,
    expected_task_id: str, expected_worker_slot: str,
    expected_assignment_sha256: str, expected_state_version: int,
    expected_artifact_sha256: str,
    artifact_commit_sha: str, artifact_path: str,
    comment_reader=None, blob_reader=None,
) -> AtomicResultForHumanReview:
    """Verify off-database, then commit all three receipt tables atomically.

    A crash/error after the base INSERT cannot expose a half-reviewed row:
    BEGIN IMMEDIATE wraps the base + Git blob receipt + text digest receipt.
    A crash during validation creates NO review rows. Explicit replay or a
    legacy orphan blocks instead of silently replaying ambiguous work.
    """
    def reply(status, reason, *, verified=False, substantive=False, written=False):
        return AtomicResultForHumanReview(
            status, reason,
            comment_and_blob_verified=verified,
            substantive_content_present=substantive,
            atomic_evidence_recorded=written,
        )

    if (not _safe_db(database, allowed_experiments_root)
        or type(comment_id) is not int or comment_id < 1
        or not isinstance(expected_artifact_sha256, str)
        or _SHA.fullmatch(expected_artifact_sha256) is None
        or not isinstance(artifact_commit_sha, str)
        or _COMMIT.fullmatch(artifact_commit_sha) is None
        or not isinstance(artifact_path, str)
        or _PATH.fullmatch(artifact_path) is None):
        return reply("BLOCKED", "ATOMIC_EXPERIMENT_SCOPE_INVALID")

    try:
        comment = (comment_reader or _gh_get_comment)(comment_id)
    except Exception:
        return reply("BLOCKED", "GITHUB_COMMENT_READER_UNAVAILABLE")
    if not isinstance(comment, dict) or comment.get("id") != comment_id:
        return reply("BLOCKED", "SCOPED_GITHUB_COMMENT_UNVERIFIED")
    body = comment.get("body")
    if (not isinstance(body, str) or len(body) > 4096
        or not body.startswith(_HEADER)):
        return reply("BLOCKED", "GITHUB_COMMENT_BODY_UNVERIFIED")
    try:
        doc = json.loads(body[len(_HEADER):])
    except (TypeError, ValueError):
        return reply("BLOCKED", "GITHUB_COMMENT_JSON_INVALID")
    if not isinstance(doc, dict) or doc.get("artifact_sha256") != expected_artifact_sha256:
        return reply("BLOCKED", "COMMENT_FILE_DIGEST_MISMATCH")

    proof = verify_immutable_github_artifact_for_review(
        path=artifact_path, commit_sha=artifact_commit_sha,
        expected_artifact_sha256=expected_artifact_sha256,
        expected_project_id=expected_project_id,
        expected_assignment_id=expected_assignment_id,
        expected_task_id=expected_task_id,
        expected_worker_slot=expected_worker_slot,
        expected_state_version=expected_state_version,
        reader=blob_reader,
        require_substantive_work_product=True,
    )
    if (not proof.content_digest_verified
        or not proof.scope_verified
        or not proof.substantive_work_product_present
        or not isinstance(proof.work_product_sha256, str)
        or _SHA.fullmatch(proof.work_product_sha256) is None):
        return reply("BLOCKED", "PINNED_SUBSTANTIVE_BLOB_NOT_VERIFIED")

    checked = stage_explicit_github_worker_artifact_for_review(
        database=database,
        allowed_experiments_root=allowed_experiments_root,
        comment=comment,
        verified_issue_number=expected_issue_number,
        allowlisted_github_author_id=allowlisted_github_author_id,
        expected_project_id=expected_project_id,
        expected_assignment_id=expected_assignment_id,
        expected_task_id=expected_task_id,
        expected_worker_slot=expected_worker_slot,
        expected_assignment_sha256=expected_assignment_sha256,
        expected_state_version=expected_state_version,
        validate_only=True,
    )
    if checked.status != "VALIDATED_NO_WRITE":
        return reply("BLOCKED", "SCOPED_COMMENT_VALIDATION_FAILED")

    project_hash = hashlib.sha256(expected_project_id.encode("ascii")).hexdigest()
    assignment_hash = hashlib.sha256(
        (expected_project_id + ":" + expected_assignment_id).encode("ascii")
    ).hexdigest()
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path_digest = hashlib.sha256(artifact_path.encode("ascii")).hexdigest()

    try:
        with contextlib.closing(sqlite3.connect(
            database, isolation_level=None, timeout=4,
        )) as db:
            # Initialize tables before transaction on this isolated DB.
            # No pre-existing legacy review evidence is mutated.
            db.executescript(_BASE_SCHEMA)
            db.executescript(_BLOB_SCHEMA)
            db.executescript(_SUBSTANTIVE_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                prior = db.execute(
                    "SELECT 1 FROM r2_github_review_artifacts "
                    "WHERE comment_id=? OR assignment_digest=?",
                    (comment_id, assignment_hash),
                ).fetchone()
                if prior is not None:
                    db.rollback()
                    return reply("BLOCKED", "EXISTING_OR_ORPHANED_COMMENT_REVIEW_NO_REPLAY")
                # Legacy ledgers could also have a blob/text receipt
                # without ANY base comment row. Since this scratch ledger
                # holds one review project, an unscoped orphan is unsafe
                # evidence; refuse all further project intake, never delete
                # or implicitly heal it.
                orphan = db.execute(
                    "SELECT 1 FROM r2_immutable_artifact_attestations e "
                    "LEFT JOIN r2_github_review_artifacts r "
                    "ON r.comment_id=e.comment_id "
                    "WHERE r.comment_id IS NULL LIMIT 1"
                ).fetchone()
                if orphan is None:
                    orphan = db.execute(
                        "SELECT 1 FROM r2_substantive_work_receipts t "
                        "LEFT JOIN r2_github_review_artifacts r "
                        "ON r.comment_id=t.comment_id "
                        "WHERE r.comment_id IS NULL LIMIT 1"
                    ).fetchone()
                if orphan is not None:
                    db.rollback()
                    return reply("BLOCKED", "UNSCOPED_LEGACY_REVIEW_ORPHAN_PRESENT")
                # The old two-stage route can leave a base comment row
                # without its Git blob/text receipt. Never append another
                # worker result to a project with pre-existing partial or
                # mismatched proofs. This is checked under the same write
                # lock as the new 3-row atomic insert (no TOCTOU race).
                existing = db.execute(
                    "SELECT r.worker_slot,r.artifact_sha256,"
                    "e.artifact_sha256,e.status,"
                    "s.deliverable_sha256,s.status "
                    "FROM r2_github_review_artifacts r "
                    "LEFT JOIN r2_immutable_artifact_attestations e "
                    "ON e.comment_id=r.comment_id "
                    "LEFT JOIN r2_substantive_work_receipts s "
                    "ON s.comment_id=r.comment_id "
                    "WHERE r.project_digest=?",
                    (project_hash,),
                ).fetchall()
                if len(existing) > 1:
                    db.rollback()
                    return reply("BLOCKED", "PROJECT_REVIEW_CAPACITY_EXCEEDED")
                for slot, base_sha, blob_sha, blob_status, text_sha, text_status in existing:
                    if (slot not in ("worker-slot-1", "worker-slot-2")
                        or blob_sha != base_sha
                        or blob_status != "GIT_BLOB_VERIFIED_FOR_HUMAN_REVIEW"
                        or not isinstance(text_sha, str) or _SHA.fullmatch(text_sha) is None
                        or text_status != "SUBSTANTIVE_TEXT_PRESENT_UNREVIEWED"):
                        db.rollback()
                        return reply("BLOCKED", "PREEXISTING_REVIEW_EVIDENCE_INCOMPLETE")
                    if slot == expected_worker_slot:
                        db.rollback()
                        return reply("BLOCKED", "PROJECT_WORKER_SLOT_ALREADY_FILLED")
                db.execute(
                    "INSERT INTO r2_github_review_artifacts "
                    "(comment_id,project_digest,assignment_digest,"
                    "assignment_sha256,expected_state_version,body_digest,"
                    "expected_issue_number,trusted_author_id,worker_slot,"
                    "artifact_sha256,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (comment_id, project_hash, assignment_hash,
                     expected_assignment_sha256, expected_state_version,
                     body_hash, expected_issue_number, allowlisted_github_author_id,
                     expected_worker_slot, expected_artifact_sha256,
                     "STAGED_FOR_REVIEW_ONLY"),
                )
                db.execute(
                    "INSERT INTO r2_immutable_artifact_attestations"
                    "(comment_id,artifact_commit_sha,artifact_path_digest,"
                    "artifact_sha256,status) VALUES(?,?,?,?,?)",
                    (comment_id, artifact_commit_sha, path_digest,
                     expected_artifact_sha256,
                     "GIT_BLOB_VERIFIED_FOR_HUMAN_REVIEW"),
                )
                db.execute(
                    "INSERT INTO r2_substantive_work_receipts"
                    "(comment_id,deliverable_sha256,status) VALUES(?,?,?)",
                    (comment_id, proof.work_product_sha256,
                     "SUBSTANTIVE_TEXT_PRESENT_UNREVIEWED"),
                )
                db.commit()
                return reply(
                    "ATOMIC_PINNED_SUBSTANTIVE_WORK_FOR_HUMAN_REVIEW",
                    "ALL_THREE_RECEIPTS_COMMITTED_NOT_GPT_TERMINAL",
                    verified=True, substantive=True, written=True,
                )
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError):
        return reply("BLOCKED", "ATOMIC_REVIEW_LEDGER_FAILED_CLOSED")
