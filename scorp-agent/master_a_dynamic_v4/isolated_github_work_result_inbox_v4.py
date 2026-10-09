"""GitHub work-artifact review inbox for isolated SCORP R2.

This observes an EXPLICIT worker-posted result artifact, NOT a ChatGPT browser
turn-complete event. GitHub author/comment IDs are supplied by the caller from
its verified API envelope; comment text is untrusted and cannot attest itself.

NO GPT wake, Chrome Use, browser send, local execution, Task Scheduler, or
production R1 writes. All accepted results are staged for MANUAL REVIEW only.
Duplicate comment, edited comment, duplicate assignment, replay and concurrent
writers fail closed. Only metadata hashes and immutable summary fields persist.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat

_HEADER = "SCORP_R2_WORK_ARTIFACT::"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^(?:assignment|task|project)-[a-z0-9-]{4,80}$")
_SLOT = frozenset({"worker-slot-1", "worker-slot-2"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS r2_github_review_artifacts (
 comment_id INTEGER PRIMARY KEY CHECK(comment_id > 0),
 assignment_digest TEXT NOT NULL UNIQUE,
 body_digest TEXT NOT NULL,
 expected_issue_number INTEGER NOT NULL,
 trusted_author_id INTEGER NOT NULL,
 worker_slot TEXT NOT NULL,
 artifact_sha256 TEXT NOT NULL,
 received_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 status TEXT NOT NULL CHECK(status='STAGED_FOR_REVIEW_ONLY')
);
"""


@dataclass(frozen=True)
class WorkArtifactReview:
    status: str
    reason: str
    staged_for_review: bool = False
    source_author_authenticated: bool = False
    chatgpt_turn_final_attested: bool = False
    browser_send_authorized: bool = False
    wake_master_authorized: bool = False
    wake_worker_authorized: bool = False
    local_execution_authorized: bool = False


def _safe_db(path: Path, root: Path) -> bool:
    if not isinstance(path, Path) or not isinstance(root, Path):
        return False
    try:
        if root.name != "experiments" or root.is_symlink() or not root.is_dir():
            return False
        if path.name != "work-artifact-review.sqlite3" or path.is_symlink():
            return False
        parent = path.parent
        if (
            not parent.name.startswith("r2-work-artifact-review-")
            or not parent.is_dir() or parent.is_symlink()
            or getattr(parent, "is_junction", lambda: False)()
            or getattr(root, "is_junction", lambda: False)()
            or parent.resolve().parent != root.resolve()
        ):
            return False
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def stage_explicit_github_worker_artifact_for_review(
    *,
    database: Path,
    allowed_experiments_root: Path,
    comment: object,
    verified_issue_number: int,
    allowlisted_github_author_id: int,
    expected_project_id: str,
    expected_assignment_id: str,
    expected_task_id: str,
    expected_worker_slot: str,
    expected_assignment_sha256: str,
    expected_state_version: int,
) -> WorkArtifactReview:
    """Strictly parse provider-supplied comment metadata and stage once."""
    def reject(reason: str, *, staged=False) -> WorkArtifactReview:
        return WorkArtifactReview(
            "STAGED_FOR_REVIEW" if staged else "BLOCKED",
            "EXPLICIT_WORK_ARTIFACT_NOT_GPT_TERMINAL_EVENT" if staged else reason,
            staged_for_review=staged,
        )

    if not _safe_db(database, allowed_experiments_root):
        return reject("ISOLATED_EXPERIMENT_DB_REQUIRED")
    if (
        type(verified_issue_number) is not int or verified_issue_number < 1
        or type(allowlisted_github_author_id) is not int or allowlisted_github_author_id < 1
        or type(expected_state_version) is not int or expected_state_version < 0
        or expected_worker_slot not in _SLOT
        or not isinstance(expected_project_id, str) or not _ID.fullmatch(expected_project_id)
        or not isinstance(expected_assignment_id, str) or not _ID.fullmatch(expected_assignment_id)
        or not isinstance(expected_task_id, str) or not _ID.fullmatch(expected_task_id)
        or not isinstance(expected_assignment_sha256, str)
        or _SHA.fullmatch(expected_assignment_sha256) is None
    ):
        return reject("EXPECTED_WORKER_ASSIGNMENT_SCOPE_INVALID")
    if not isinstance(comment, dict):
        return reject("GITHUB_COMMENT_ENVELOPE_REQUIRED")
    ident = comment.get("id")
    actor = comment.get("user")
    body = comment.get("body")
    if (
        type(ident) is not int or ident < 1
        or not isinstance(actor, dict)
        or type(actor.get("id")) is not int
        or actor["id"] != allowlisted_github_author_id
    ):
        return reject("GITHUB_AUTHOR_OR_COMMENT_ID_MISMATCH")
    # The provider issues/comments deep link must point to the EXACT issue
    # and immutable comment id. Caller-side issue-number assertions alone
    # must not let one issue's work artifact masquerade as another.
    expected_url = (
        "https://github.com/Scorp96/scorp-control-plane/issues/"
        + str(verified_issue_number) + "#issuecomment-" + str(ident)
    )
    if comment.get("url") != expected_url:
        return reject("GITHUB_ISSUE_OR_COMMENT_LINK_UNVERIFIED")
    created = _timestamp(comment.get("created_at"))
    updated = _timestamp(comment.get("updated_at"))
    if created is None or updated is None or updated != created:
        return reject("GITHUB_COMMENT_EDIT_OR_TIME_UNVERIFIED")
    if (
        not isinstance(body, str) or len(body) > 4096
        or not body.startswith(_HEADER)
        or "\n" in body or "\r" in body
    ):
        return reject("WORK_ARTIFACT_BODY_SHAPE_INVALID")
    try:
        document = json.loads(body[len(_HEADER):])
    except (ValueError, TypeError):
        return reject("WORK_ARTIFACT_JSON_INVALID")
    expected_keys = {
        "protocol", "project_id", "assignment_id", "task_id", "worker_slot",
        "assignment_sha256", "artifact_sha256", "state_version", "event",
    }
    if not isinstance(document, dict) or set(document) != expected_keys:
        return reject("WORK_ARTIFACT_PROTOCOL_FIELDS_INVALID")
    if (
        document.get("protocol") != "scorp.github-work-artifact/1"
        or document.get("event") != "ARTIFACT_STAGED_FOR_REVIEW"
        or document.get("project_id") != expected_project_id
        or document.get("assignment_id") != expected_assignment_id
        or document.get("task_id") != expected_task_id
        or document.get("worker_slot") != expected_worker_slot
        or document.get("assignment_sha256") != expected_assignment_sha256
        or type(document.get("state_version")) is not int
        or document["state_version"] != expected_state_version
        or not isinstance(document.get("artifact_sha256"), str)
        or _SHA.fullmatch(document["artifact_sha256"]) is None
    ):
        return reject("WORK_ARTIFACT_SCOPE_OR_DIGEST_INVALID")
    assignment_hash = hashlib.sha256(
        (expected_project_id + ":" + expected_assignment_id).encode("ascii")
    ).hexdigest()
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    try:
        with contextlib.closing(sqlite3.connect(
            database, isolation_level=None, timeout=4,
        )) as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT 1 FROM r2_github_review_artifacts "
                    "WHERE comment_id=? OR assignment_digest=?",
                    (ident, assignment_hash),
                ).fetchone()
                if existing:
                    db.rollback()
                    return reject("DURABLE_WORK_ARTIFACT_OR_ASSIGNMENT_ALREADY_SEEN")
                db.execute(
                    "INSERT INTO r2_github_review_artifacts "
                    "(comment_id,assignment_digest,body_digest,"
                    "expected_issue_number,trusted_author_id,worker_slot,"
                    "artifact_sha256,status) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        ident, assignment_hash, body_hash,
                        verified_issue_number, allowlisted_github_author_id,
                        expected_worker_slot, document["artifact_sha256"],
                        "STAGED_FOR_REVIEW_ONLY",
                    ),
                )
                db.commit()
                return reject("", staged=True)
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError):
        return reject("WORK_ARTIFACT_JOURNAL_UNAVAILABLE")
