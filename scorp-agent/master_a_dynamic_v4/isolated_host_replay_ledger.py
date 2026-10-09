"""Experimental durable anti-replay latch for signed GPT terminal receipts.

This module has NO browser transport, model access, production state or
authorization for sending a message. Its SQLite write is allowed ONLY to an
explicitly configured, pre-existing r2-* child of a directory literally named
experiments. The local host signer / actual event producer is STILL absent.
Reserve means reserved FOR REVIEW ONLY, never permission to send.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import pathlib
import re
import sqlite3
from typing import Sequence

from .host_terminal_receipt import HostTerminalReceipt
from .turn_completion_evidence import TurnSample, assess_turn_completion


_EVENT = re.compile(r"^[0-9a-f]{32}$")
_DATABASE_NAME = "host-terminal-replay-ledger.sqlite3"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS host_terminal_events(
  event_key TEXT PRIMARY KEY,
  scope_key TEXT NOT NULL,
  event_sequence INTEGER NOT NULL,
  receipt_sha256 TEXT NOT NULL,
  reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS host_terminal_scope_cursors(
  scope_key TEXT PRIMARY KEY,
  high_sequence INTEGER NOT NULL CHECK(high_sequence >= 0)
);
"""


@dataclasses.dataclass(frozen=True)
class ReplayReservation:
    status: str
    reason: str
    browser_send_authorized: bool = False
    terminal_event_reserved_for_review: bool = False


def _safe_db_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    if not isinstance(path, pathlib.Path) or not isinstance(root, pathlib.Path):
        return False
    if root.name != "experiments" or not root.is_dir() or root.is_symlink():
        return False
    if path.name != _DATABASE_NAME or path.is_symlink():
        return False
    root_resolved = root.resolve()
    parent = path.parent
    if parent.name[:3] != "r2-" or not parent.is_dir() or parent.is_symlink():
        return False
    if parent.resolve().parent != root_resolved:
        return False
    return path.resolve().parent == parent.resolve()


def _sha_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def reserve_terminal_receipt_for_review(
    database: pathlib.Path,
    *,
    allowed_experiments_root: pathlib.Path,
    samples: Sequence[TurnSample],
    receipts: Sequence[HostTerminalReceipt] | None,
    host_attestation_key: bytes | None,
    expected_session_id: str,
    expected_conversation_url: str,
    expected_binding_generation: int,
    expected_intent_id: str,
) -> ReplayReservation:
    """Atomic replay latch AFTER verified terminal proof; no message send.

    An accepted reservation only demonstrates local signature, scope and
    sequence invariants. It is NOT itself a trusted UI-origin event or a
    browser-submit permission. The caller must separately verify that the
    host signer has an independently trusted final-event source.
    """
    def fail(reason: str) -> ReplayReservation:
        return ReplayReservation("BLOCKED", reason)

    if not _safe_db_path(database, allowed_experiments_root):
        return fail("EXPERIMENT_SCOPE_INVALID")
    completed = assess_turn_completion(
        samples,
        expected_session_id=expected_session_id,
        expected_conversation_url=expected_conversation_url,
        expected_binding_generation=expected_binding_generation,
        expected_intent_id=expected_intent_id,
        host_receipts=receipts,
        host_attestation_key=host_attestation_key,
    )
    if completed.status != "IDLE_CONFIRMED":
        return fail("TURN_PROOF_UNVERIFIED:" + completed.reason)
    if receipts is None or len(receipts) != 2:
        return fail("TWO_TERMINAL_RECEIPTS_REQUIRED")
    a, b = receipts
    if not isinstance(b.event_id, str) or not _EVENT.fullmatch(b.event_id):
        return fail("TERMINAL_EVENT_ID_INVALID")

    scope_key = _sha_json({
        "session_id": expected_session_id,
        "conversation_url": expected_conversation_url,
        "binding_generation": expected_binding_generation,
    })
    event_key = _sha_json({
        "scope_key": scope_key,
        "event_id": b.event_id,
        "intent_id": expected_intent_id,
    })
    receipt_digest = _sha_json(dataclasses.asdict(b))
    try:
        # No production DB can be reached unless a caller explicitly
        # violates the experiments-root check above. Never open a browser.
        with contextlib.closing(sqlite3.connect(str(database), timeout=3, isolation_level=None)) as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                exists = db.execute(
                    "SELECT receipt_sha256 FROM host_terminal_events WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if exists is not None:
                    db.rollback()
                    return ReplayReservation("ALREADY_RESERVED", "TERMINAL_EVENT_REPLAY")
                current = db.execute(
                    "SELECT high_sequence FROM host_terminal_scope_cursors WHERE scope_key=?",
                    (scope_key,),
                ).fetchone()
                if current is not None and b.sequence <= current[0]:
                    db.rollback()
                    return fail("NON_MONOTONIC_HOST_SEQUENCE")
                db.execute(
                    "INSERT INTO host_terminal_events(event_key,scope_key,event_sequence,receipt_sha256)"
                    " VALUES(?,?,?,?)",
                    (event_key, scope_key, b.sequence, receipt_digest),
                )
                db.execute(
                    "INSERT INTO host_terminal_scope_cursors(scope_key,high_sequence) VALUES(?,?)"
                    " ON CONFLICT(scope_key) DO UPDATE SET high_sequence=excluded.high_sequence",
                    (scope_key, b.sequence),
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return fail("EXPERIMENT_REPLAY_LEDGER_UNAVAILABLE")
    return ReplayReservation(
        "RESERVED_FOR_REVIEW", "SIGNED_TERMINAL_REPLAY_LATCHED",
        terminal_event_reserved_for_review=True,
    )
