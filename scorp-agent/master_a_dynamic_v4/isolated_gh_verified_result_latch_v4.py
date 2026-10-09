"""Pinned GitHub comment+actual immutable blob review latch for SCORP R2.

Two independent read-only GitHub API GETs must agree: comment's declared
SHA256 and a file's true bytes at a pre-authorized 40-hex immutable commit.
Only then record a durable review attestation. NOT host terminal proof,
NOT Worker identity, NOT authorization to wake or send.

If interrupted between base-result staging and blob-attestation insertion,
the missing proof fails closed. Never replay an ambiguous old submission.
"""
from __future__ import annotations
import contextlib
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from .isolated_github_work_result_inbox_v4 import (
    _safe_db, _HEADER, _SHA, stage_explicit_github_worker_artifact_for_review,
)
from .isolated_gh_work_artifact_transport_v4 import _gh_get_comment
from .isolated_gh_immutable_artifact_v4 import (
    _PATH, _COMMIT, verify_immutable_github_artifact_for_review,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS r2_immutable_artifact_attestations (
  comment_id INTEGER PRIMARY KEY,
  artifact_commit_sha TEXT NOT NULL,
  artifact_path_digest TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status='GIT_BLOB_VERIFIED_FOR_HUMAN_REVIEW')
);
"""

@dataclass(frozen=True)
class VerifiedWorkArtifactIntake:
    status: str
    reason: str
    comment_and_blob_verified: bool = False
    evidence_recorded: bool = False
    actual_worker_identity_verified: bool = False
    host_terminal_event_attested: bool = False
    browser_send_authorized: bool = False
    wake_master_authorized: bool = False
    wake_worker_authorized: bool = False
    local_execution_authorized: bool = False

def stage_pinned_github_blob_comment_for_review(
    *, database: Path, allowed_experiments_root: Path,
    comment_id: int, expected_issue_number: int,
    allowlisted_github_author_id: int,
    expected_project_id: str, expected_assignment_id: str,
    expected_task_id: str, expected_worker_slot: str,
    expected_assignment_sha256: str, expected_state_version: int,
    expected_artifact_sha256: str,
    artifact_commit_sha: str, artifact_path: str,
    comment_reader=None, blob_reader=None,
) -> VerifiedWorkArtifactIntake:
    def result(status, reason, checked=False, persisted=False):
        return VerifiedWorkArtifactIntake(
            status,reason,comment_and_blob_verified=checked,
            evidence_recorded=persisted,
        )
    if (not _safe_db(database,allowed_experiments_root)
        or type(comment_id) is not int or comment_id < 1
        or not isinstance(expected_artifact_sha256,str)
        or not _SHA.fullmatch(expected_artifact_sha256)
        or not isinstance(artifact_commit_sha,str)
        or not _COMMIT.fullmatch(artifact_commit_sha)
        or not isinstance(artifact_path,str) or not _PATH.fullmatch(artifact_path)):
        return result("BLOCKED","PINNED_COMMENT_BLOB_CONTRACT_INVALID")
    try:
        comment = (comment_reader or _gh_get_comment)(comment_id)
    except Exception:
        return result("BLOCKED","COMMENT_GET_UNAVAILABLE")
    if not isinstance(comment,dict) or comment.get("id") != comment_id:
        return result("BLOCKED","COMMENT_ENVELOPE_UNVERIFIED")
    body=comment.get("body")
    if not isinstance(body,str) or len(body)>4096 or not body.startswith(_HEADER):
        return result("BLOCKED","COMMENT_RESULT_BODY_INVALID")
    try:
        doc=json.loads(body[len(_HEADER):])
    except (TypeError,ValueError):
        return result("BLOCKED","COMMENT_RESULT_JSON_INVALID")
    if (not isinstance(doc,dict)
        or doc.get("artifact_sha256") != expected_artifact_sha256):
        return result("BLOCKED","COMMENT_ARTIFACT_DIGEST_BINDING_MISMATCH")
    proof = verify_immutable_github_artifact_for_review(
        path=artifact_path, commit_sha=artifact_commit_sha,
        expected_artifact_sha256=expected_artifact_sha256,
        expected_project_id=expected_project_id,
        expected_assignment_id=expected_assignment_id,
        expected_task_id=expected_task_id,
        expected_worker_slot=expected_worker_slot,
        expected_state_version=expected_state_version,
        reader=blob_reader,
    )
    if not proof.content_digest_verified or not proof.scope_verified:
        return result("BLOCKED","REAL_IMMUTABLE_BLOB_PROOF_REQUIRED")
    stage = stage_explicit_github_worker_artifact_for_review(
        database=database,allowed_experiments_root=allowed_experiments_root,
        comment=comment,verified_issue_number=expected_issue_number,
        allowlisted_github_author_id=allowlisted_github_author_id,
        expected_project_id=expected_project_id,
        expected_assignment_id=expected_assignment_id,
        expected_task_id=expected_task_id,
        expected_worker_slot=expected_worker_slot,
        expected_assignment_sha256=expected_assignment_sha256,
        expected_state_version=expected_state_version,
    )
    if not stage.staged_for_review:
        return result("BLOCKED",stage.reason)
    # A crash here is safe: only the unverified base review row remains.
    # Every verified-artifact barrier requires this second receipt.
    try:
        with contextlib.closing(sqlite3.connect(
            database,isolation_level=None,timeout=4,
        )) as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute(
                    "SELECT 1 FROM r2_immutable_artifact_attestations "
                    "WHERE comment_id=?",(comment_id,),
                ).fetchone() is not None:
                    db.rollback()
                    return result("BLOCKED","BLOB_ATTESTATION_REPLAY_BLOCKED")
                existing = db.execute(
                    "SELECT 1 FROM r2_github_review_artifacts "
                    "WHERE comment_id=? AND artifact_sha256=? "
                    "AND status='STAGED_FOR_REVIEW_ONLY'",
                    (comment_id,expected_artifact_sha256),
                ).fetchone()
                if existing is None:
                    db.rollback()
                    return result("BLOCKED","BASE_COMMENT_REVIEW_ROW_MISSING")
                db.execute(
                    "INSERT INTO r2_immutable_artifact_attestations "
                    "(comment_id,artifact_commit_sha,artifact_path_digest,"
                    "artifact_sha256,status) VALUES(?,?,?,?,?)",
                    (comment_id,artifact_commit_sha,
                     hashlib.sha256(artifact_path.encode("ascii")).hexdigest(),
                     expected_artifact_sha256,
                     "GIT_BLOB_VERIFIED_FOR_HUMAN_REVIEW"),
                )
                db.commit()
                return result(
                    "BLOB_AND_COMMENT_STAGED_FOR_REVIEW",
                    "PINNED_GIT_BLOB_AND_COMMENT_BOUND_NO_GPT_WAKE",
                    True,True,
                )
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error,OSError,ValueError,TypeError):
        return result("BLOCKED","BLOB_REVIEW_ATTESTATION_UNAVAILABLE")
