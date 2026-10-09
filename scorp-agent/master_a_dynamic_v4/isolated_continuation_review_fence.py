"""Durable no-send dispatch fence over isolated R2 verified candidates.

This is NOT a browser sender, actor, model runner or a production release.
An already-verified candidate may be claimed once FOR REVIEW ONLY. Restart,
concurrency and ambiguous outcomes cannot create a second claim.

The actual GPT terminal host issuer is absent. Therefore even a first claim
never grants a browser-send or local-execution capability.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
import pathlib
import re
import sqlite3

from .isolated_host_replay_ledger import _safe_db_path


_KEY = re.compile(r"^continue-[0-9a-f]{64}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS host_continuation_review_fences(
  idempotency_key TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  proof_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN
    ('CLAIMED_FOR_REVIEW','BLOCKED_AMBIGUOUS','CLOSED_NO_SEND')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(frozen=True)
class ReviewFenceDecision:
    status: str
    reason: str
    browser_send_authorized: bool = False
    local_execution_authorized: bool = False
    candidate_claimed_for_review: bool = False


def claim_candidate_for_no_send_review(
    database: pathlib.Path,
    *,
    allowed_experiments_root: pathlib.Path,
    idempotency_key: str,
    expected_project_id: str,
    expected_decision_id: str,
) -> ReviewFenceDecision:
    """Transactional first-claim. A repeated claim is NEVER ready to dispatch."""
    def result(status: str, reason: str, *, claimed: bool = False):
        return ReviewFenceDecision(
            status, reason, candidate_claimed_for_review=claimed,
        )
    if not _safe_db_path(database, allowed_experiments_root):
        return result("BLOCKED", "EXPERIMENT_SCOPE_INVALID")
    if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
        return result("BLOCKED", "CONTINUATION_KEY_INVALID")
    if (
        not isinstance(expected_project_id, str) or not expected_project_id.strip()
        or not isinstance(expected_decision_id, str)
        or not expected_decision_id.startswith("decision-")
    ):
        return result("BLOCKED", "EXPECTED_DECISION_INVALID")
    # Missing database is not an implicit authorization to create anything.
    if not database.is_file():
        return result("BLOCKED", "VERIFIED_CANDIDATE_LEDGER_MISSING")

    try:
        with contextlib.closing(sqlite3.connect(
            str(database), isolation_level=None, timeout=5,
        )) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # This CREATE is itself inside BEGIN IMMEDIATE. A rolled-back
                # claim does not implicitly initialize the outbox.
                db.execute("""
                  CREATE TABLE IF NOT EXISTS host_continuation_review_fences(
                    idempotency_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    proof_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                      'CLAIMED_FOR_REVIEW','BLOCKED_AMBIGUOUS','CLOSED_NO_SEND'
                    )),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                  )
                """)
                candidate = db.execute(
                    "SELECT project_id,decision_id,proof_sha256,event_key "
                    "FROM host_continuation_candidates WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if candidate is None:
                    db.rollback()
                    return result("BLOCKED", "VERIFIED_CANDIDATE_NOT_FOUND")
                project_id, decision_id, digest, event_key = candidate
                if (
                    project_id != expected_project_id
                    or decision_id != expected_decision_id
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                ):
                    db.rollback()
                    return result("BLOCKED", "DURABLE_DECISION_BINDING_MISMATCH")
                # Both event reservation and global UUID fence MUST exist,
                # preventing a legacy or partial ledger row from being claimed.
                event = db.execute(
                    "SELECT scope_key,event_sequence FROM host_terminal_events "
                    "WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                global_event = db.execute(
                    "SELECT 1 FROM host_terminal_global_ids WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                cursor = (
                    db.execute(
                        "SELECT high_sequence FROM host_terminal_scope_cursors "
                        "WHERE scope_key=?",
                        (event[0],),
                    ).fetchone()
                    if event is not None else None
                )
                if (event is None or global_event is None or cursor is None
                        or cursor[0] < event[1]):
                    db.rollback()
                    return result("BLOCKED", "TERMINAL_EVENT_LEDGER_INCOMPLETE")

                existing = db.execute(
                    "SELECT status FROM host_continuation_review_fences "
                    "WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    db.rollback()
                    return result("ALREADY_CLAIMED", "DURABLE_REVIEW_FENCE_EXISTS")
                db.execute(
                    "INSERT INTO host_continuation_review_fences("
                    "idempotency_key,project_id,decision_id,proof_sha256,status"
                    ") VALUES(?,?,?,?,?)",
                    (idempotency_key, project_id, decision_id, digest,
                     "CLAIMED_FOR_REVIEW"),
                )
                db.commit()
                return result(
                    "CLAIMED_FOR_REVIEW", "ONE_SHOT_REVIEW_ONLY_NO_HOST_DISPATCH",
                    claimed=True,
                )
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        return result("BLOCKED", "REVIEW_FENCE_LEDGER_UNAVAILABLE")


def block_claimed_candidate_without_send(
    database: pathlib.Path,
    *,
    allowed_experiments_root: pathlib.Path,
    idempotency_key: str,
    expected_project_id: str,
) -> ReviewFenceDecision:
    """Monotonic ambiguity fence: no replay after a partially observed run."""
    def result(status: str, reason: str):
        return ReviewFenceDecision(status, reason)
    if not _safe_db_path(database, allowed_experiments_root):
        return result("BLOCKED", "EXPERIMENT_SCOPE_INVALID")
    if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
        return result("BLOCKED", "CONTINUATION_KEY_INVALID")
    if not isinstance(expected_project_id, str) or not expected_project_id.strip():
        return result("BLOCKED", "EXPECTED_PROJECT_INVALID")
    if not database.is_file():
        return result("BLOCKED", "VERIFIED_CANDIDATE_LEDGER_MISSING")
    try:
        with contextlib.closing(sqlite3.connect(
            str(database), isolation_level=None, timeout=5,
        )) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # An absent review table positively proves there has not been
                # any prior claim. Avoid conflating it with DB corruption,
                # and never create the table in this monotonic block path.
                table_present = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='host_continuation_review_fences'"
                ).fetchone()
                if table_present is None:
                    db.rollback()
                    return result("BLOCKED", "REVIEW_CLAIM_NOT_VERIFIED")
                row = db.execute(
                    "SELECT project_id,status FROM host_continuation_review_fences "
                    "WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if row is None or row[0] != expected_project_id:
                    db.rollback()
                    return result("BLOCKED", "REVIEW_CLAIM_NOT_VERIFIED")
                if row[1] == "BLOCKED_AMBIGUOUS":
                    db.rollback()
                    return result("ALREADY_BLOCKED", "AMBIGUOUS_REVIEW_PERSISTED")
                if row[1] != "CLAIMED_FOR_REVIEW":
                    db.rollback()
                    return result("BLOCKED", "REVIEW_FENCE_NOT_CLAIMABLE")
                db.execute(
                    "UPDATE host_continuation_review_fences "
                    "SET status='BLOCKED_AMBIGUOUS',changed_at=CURRENT_TIMESTAMP "
                    "WHERE idempotency_key=? AND status='CLAIMED_FOR_REVIEW'",
                    (idempotency_key,),
                )
                db.commit()
                return result("BLOCKED_AMBIGUOUS", "MANUAL_RECONCILIATION_REQUIRED")
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        return result("BLOCKED", "REVIEW_FENCE_LEDGER_UNAVAILABLE")
