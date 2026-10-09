"""One-shot isolated ChatGPT Worker browser-launch ledger; no browser execution.

Context: an explicit --launch ChatGPT home-page canary produced a timeout
but a named Chrome Use daemon subsequently existed; repeating tab-list probes
appeared correlated with new Chrome profile process fanout.

This isolated ledger must reserve a single external boundary BEFORE running
the browser command. UNKNOWN outcome is permanent no-retry without a new,
separate operator-reviewed assignment. This module never launches, sends,
navigates, stops, kills, stores credentials, or edits the original R1.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from pathlib import Path
import re
import sqlite3

_ATTEMPT = re.compile(r"^[0-9a-f]{32}$")
_NAMESPACE = re.compile(r"^scorp-r2-worker2-[a-z0-9-]{12,80}$")
_SUBDIR = re.compile(r"^r2-browser-launch-[a-z0-9-]{8,50}$")
_ORIGIN = "https://chatgpt.com"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_launch_attempts (
  attempt_id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL UNIQUE,
  target_origin TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN (
    'RESERVED_NO_EXTERNAL_ACTION',
    'EXTERNAL_LAUNCH_EDGE_RESERVED',
    'COMMAND_RETURNED_UNVERIFIED',
    'LAUNCH_TIMED_OUT_UNVERIFIED',
    'ISOLATED_SESSION_STOPPED_FOR_REVIEW')),
  created_utc TEXT NOT NULL,
  edge_reserved_utc TEXT,
  reviewed_utc TEXT
);
"""


@dataclass(frozen=True)
class LaunchGate:
    status: str
    reason: str
    attempt_recorded: bool = False
    external_edge_reserved: bool = False
    browser_or_model_send_authorized: bool = False
    original_master_resume_authorized: bool = False
    independent_gpt_worker_authenticated: bool = False


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _safe_db(database: object, experiments_root: object) -> bool:
    """Exactly ONE child of experiments, no links or arbitrary file targets."""
    if not isinstance(database, Path) or not isinstance(experiments_root, Path):
        return False
    try:
        if (not experiments_root.is_dir() or experiments_root.is_symlink()
            or not database.is_absolute() or database.is_symlink()
            or database.name != "launch_attempts.sqlite3"
            or not database.parent.is_dir() or database.parent.is_symlink()
            or _SUBDIR.fullmatch(database.parent.name) is None):
            return False
        root = experiments_root.resolve(strict=True)
        parent = database.parent.resolve(strict=True)
        return parent.parent == root
    except (OSError, RuntimeError, ValueError):
        return False


def _check_input(
    database: object,
    experiments_root: object,
    attempt_id: object,
    namespace: object,
) -> str | None:
    if not _safe_db(database, experiments_root):
        return "ISOLATED_EXPERIMENT_DATABASE_SCOPE_INVALID"
    if not isinstance(attempt_id, str) or not _ATTEMPT.fullmatch(attempt_id):
        return "ATTEMPT_ID_INVALID"
    if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
        return "NAMESPACE_INVALID"
    return None


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=3, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=3000")
    return connection


def reserve_isolated_browser_launch(
    *,
    database: Path,
    experiments_root: Path,
    attempt_id: str,
    namespace: str,
    target_origin: str,
) -> LaunchGate:
    """Reserve one immutable namespace/attempt; no external browser action."""
    reason = _check_input(database, experiments_root, attempt_id, namespace)
    if reason:
        return LaunchGate("BLOCKED", reason)
    if target_origin != _ORIGIN:
        return LaunchGate("BLOCKED", "TARGET_ORIGIN_NOT_ALLOWLISTED")
    try:
        with _connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_SCHEMA)
            exists = db.execute(
                "SELECT 1 FROM browser_launch_attempts "
                "WHERE attempt_id=? OR namespace=? LIMIT 1",
                (attempt_id, namespace),
            ).fetchone()
            if exists is not None:
                db.rollback()
                return LaunchGate("BLOCKED", "EXISTING_OR_AMBIGUOUS_ATTEMPT_NO_REPLAY")
            db.execute(
                "INSERT INTO browser_launch_attempts("
                "attempt_id,namespace,target_origin,state,created_utc)"
                "VALUES(?,?,?,?,?)",
                (attempt_id, namespace, target_origin,
                 "RESERVED_NO_EXTERNAL_ACTION", _utc()),
            )
            db.commit()
        return LaunchGate("RESERVED", "NO_BROWSER_ACTION_OCCURRED", True)
    except (OSError, sqlite3.Error, ValueError):
        return LaunchGate("BLOCKED", "LEDGER_WRITE_OR_INTEGRITY_UNAVAILABLE")


def reserve_external_launch_edge_once(
    *,
    database: Path,
    experiments_root: Path,
    attempt_id: str,
    namespace: str,
) -> LaunchGate:
    """Durable one-shot edge; caller may *separately* perform a host action.

    Any crash after this method returns permanently blocks automatic retry.
    The successful outcome is NOT a browser/session/ChatGPT authentication.
    """
    reason = _check_input(database, experiments_root, attempt_id, namespace)
    if reason:
        return LaunchGate("BLOCKED", reason)
    if not database.is_file():
        return LaunchGate("BLOCKED", "LEDGER_NOT_FOUND")
    try:
        with _connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,target_origin FROM browser_launch_attempts "
                "WHERE attempt_id=? AND namespace=?",
                (attempt_id, namespace),
            ).fetchone()
            if row != ("RESERVED_NO_EXTERNAL_ACTION", _ORIGIN):
                db.rollback()
                return LaunchGate("BLOCKED", "LAUNCH_EDGE_ALREADY_CONSUMED_OR_UNKNOWN")
            db.execute(
                "UPDATE browser_launch_attempts "
                "SET state='EXTERNAL_LAUNCH_EDGE_RESERVED',edge_reserved_utc=? "
                "WHERE attempt_id=? AND namespace=?",
                (_utc(), attempt_id, namespace),
            )
            db.commit()
        return LaunchGate("EXTERNAL_EDGE_RESERVED",
                          "SINGLE_EXTERNAL_ATTEMPT_ONLY_NO_SEND_PERMISSION",
                          attempt_recorded=True, external_edge_reserved=True)
    except (OSError, sqlite3.Error, ValueError):
        return LaunchGate("BLOCKED", "LEDGER_WRITE_OR_INTEGRITY_UNAVAILABLE")


def record_host_review_only(
    *,
    database: Path,
    experiments_root: Path,
    attempt_id: str,
    namespace: str,
    observed_status: str,
) -> LaunchGate:
    """Record a human/host review hint, never authorize a replay or send.

    Not a trustworthy browser event signature or login/terminal attestation.
    """
    reason = _check_input(database, experiments_root, attempt_id, namespace)
    if reason:
        return LaunchGate("BLOCKED", reason)
    allowed = {
        "COMMAND_RETURNED_UNVERIFIED",
        "LAUNCH_TIMED_OUT_UNVERIFIED",
        "ISOLATED_SESSION_STOPPED_FOR_REVIEW",
    }
    if observed_status not in allowed:
        return LaunchGate("BLOCKED", "REVIEW_STATUS_NOT_ALLOWLISTED")
    if not database.is_file():
        return LaunchGate("BLOCKED", "LEDGER_NOT_FOUND")
    try:
        with _connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM browser_launch_attempts "
                "WHERE attempt_id=? AND namespace=?",
                (attempt_id, namespace),
            ).fetchone()
            if row is None or row[0] not in {
                "EXTERNAL_LAUNCH_EDGE_RESERVED",
                "LAUNCH_TIMED_OUT_UNVERIFIED",
                "COMMAND_RETURNED_UNVERIFIED",
            }:
                db.rollback()
                return LaunchGate("BLOCKED", "REVIEW_TRANSITION_NOT_AUTHORIZED")
            if (row[0] != "EXTERNAL_LAUNCH_EDGE_RESERVED"
                and observed_status != "ISOLATED_SESSION_STOPPED_FOR_REVIEW"):
                db.rollback()
                return LaunchGate("BLOCKED", "REVIEW_OUTCOME_ALREADY_RECORDED")
            db.execute(
                "UPDATE browser_launch_attempts SET state=?,reviewed_utc=? "
                "WHERE attempt_id=? AND namespace=?",
                (observed_status, _utc(), attempt_id, namespace),
            )
            db.commit()
        return LaunchGate("RECORDED_FOR_REVIEW", "NO_BROWSER_OR_MODEL_AUTHORITY",
                          attempt_recorded=True, external_edge_reserved=True)
    except (OSError, sqlite3.Error, ValueError):
        return LaunchGate("BLOCKED", "LEDGER_WRITE_OR_INTEGRITY_UNAVAILABLE")


def read_attempt_state(
    *,
    database: Path,
    experiments_root: Path,
    attempt_id: str,
    namespace: str,
) -> LaunchGate:
    """Read-only ledger observation; never return a session ID or URL."""
    reason = _check_input(database, experiments_root, attempt_id, namespace)
    if reason:
        return LaunchGate("BLOCKED", reason)
    if not database.is_file():
        return LaunchGate("BLOCKED", "LEDGER_NOT_FOUND")
    try:
        with sqlite3.connect(database.resolve().as_uri()+"?mode=ro",
                             uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT state FROM browser_launch_attempts "
                "WHERE attempt_id=? AND namespace=?",
                (attempt_id, namespace),
            ).fetchone()
        if row is None:
            return LaunchGate("BLOCKED", "ATTEMPT_NOT_FOUND")
        return LaunchGate("REVIEW_ONLY", row[0],
                          attempt_recorded=True,
                          external_edge_reserved=row[0] != "RESERVED_NO_EXTERNAL_ACTION")
    except (OSError, sqlite3.Error, ValueError):
        return LaunchGate("BLOCKED", "LEDGER_READ_UNAVAILABLE")
