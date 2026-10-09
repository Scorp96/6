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
from .session_admission import _canonical_conversation_url
from .turn_completion_evidence import TurnSample, assess_turn_completion


_EVENT = re.compile(r"^[0-9a-f]{32}$")
_DATABASE_NAME = "host-terminal-replay-ledger.sqlite3"
# Existing R2 task/state directories must stay bit-for-bit untouched.
_PROTECTED_R2_FOLDERS = frozenset({
    "r2-gpt-session-audit-20261009",
    "r2-observer-state-20261009",
})
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
CREATE TABLE IF NOT EXISTS host_terminal_global_ids(
  event_id_sha256 TEXT PRIMARY KEY,
  event_key TEXT NOT NULL UNIQUE,
  reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    try:
        if root.name != "experiments" or not root.is_dir() or root.is_symlink():
            return False
        if getattr(root, "is_junction", lambda: False)():
            return False
        if path.name != _DATABASE_NAME or path.is_symlink():
            return False
        parent = path.parent
        if (
            not parent.name.startswith("r2-")
            or parent.name.lower() in _PROTECTED_R2_FOLDERS
            or not parent.is_dir()
            or parent.is_symlink()
            or getattr(parent, "is_junction", lambda: False)()
        ):
            return False
        if path.exists():
            # A hard link to R1 SQLite passes resolve()/is_symlink().
            # Never write through such an existing on-disk alias.
            if not path.is_file() or path.stat().st_nlink != 1:
                return False
        root_resolved = root.resolve()
        if parent.resolve().parent != root_resolved:
            return False
        return path.resolve().parent == parent.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


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

    # assess_turn_completion compares canonical URLs. The durable replay
    # scope MUST use that same canonical identity, not caller-supplied URL
    # whitespace/aliases, or one signed event could receive two scope keys.
    canonical_url = _canonical_conversation_url(expected_conversation_url)
    if not canonical_url:
        return fail("EXPECTED_CONVERSATION_URL_INVALID")
    scope_key = _sha_json({
        "session_id": expected_session_id,
        "conversation_url": canonical_url,
        "binding_generation": expected_binding_generation,
    })
    event_key = _sha_json({
        "scope_key": scope_key,
        "event_id": b.event_id,
        "intent_id": expected_intent_id,
    })
    receipt_digest = _sha_json(dataclasses.asdict(b))
    # Event UUIDs are global across all physical Worker/Master sessions.
    # Scope-only hashes are insufficient when the same signed event is
    # misattributed to a second host session during recovery.
    event_id_sha256 = hashlib.sha256(b.event_id.encode("ascii")).hexdigest()
    try:
        # No production DB can be reached unless a caller explicitly
        # violates the experiments-root check above. Never open a browser.
        with contextlib.closing(sqlite3.connect(str(database), timeout=3, isolation_level=None)) as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                # Legacy R2 experimental ledgers cannot reconstruct raw event
                # UUIDs from one-way event_key hashes. Reject mixed migrations
                # until an operator explicitly discards that isolated fixture.
                prior_count = db.execute(
                    "SELECT COUNT(*) FROM host_terminal_events"
                ).fetchone()[0]
                global_count = db.execute(
                    "SELECT COUNT(*) FROM host_terminal_global_ids"
                ).fetchone()[0]
                if prior_count != global_count:
                    db.rollback()
                    return fail("LEGACY_EVENT_GLOBAL_INDEX_UNVERIFIED")
                exists = db.execute(
                    "SELECT receipt_sha256 FROM host_terminal_events WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if exists is not None:
                    db.rollback()
                    return ReplayReservation("ALREADY_RESERVED", "TERMINAL_EVENT_REPLAY")
                globally_seen = db.execute(
                    "SELECT 1 FROM host_terminal_global_ids WHERE event_id_sha256=?",
                    (event_id_sha256,),
                ).fetchone()
                if globally_seen is not None:
                    db.rollback()
                    return fail("TERMINAL_EVENT_ID_SCOPE_REPLAY")
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
                    "INSERT INTO host_terminal_global_ids(event_id_sha256,event_key) VALUES(?,?)",
                    (event_id_sha256, event_key),
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
