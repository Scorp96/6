"""Atomic no-send composition of host receipt replay and continuation intent.

ISOLATED R2 EXPERIMENT ONLY. Signed fixtures prove local integrity, not the
existence or provenance of a real GPT terminal event. No browser integration,
host signer, production DB, scheduler or dispatch authorization lives here.

An R2 continuation candidate and its terminal receipt MUST commit together.
A crash/SQLite error leaves neither, and a resumed run cannot use a spent
event or key to create another candidate.
"""
from __future__ import annotations

import contextlib
import dataclasses
import pathlib
import sqlite3
from typing import Sequence

from .continuation_gate import ContinuationRequest
from .host_terminal_receipt import HostTerminalReceipt
from .isolated_host_replay_ledger import _SCHEMA, _safe_db_path, _sha_json
from .session_admission import AdmissionPolicy, SessionObservation, _canonical_conversation_url
from .turn_completion_evidence import TurnSample
from .verified_continuation import plan_with_verified_turn


_EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS host_continuation_candidates(
    idempotency_key TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    proof_sha256 TEXT NOT NULL,
    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_RESUMABLE = frozenset({
    "WAKE_MASTER", "REASON_MASTER", "RESUME_MASTER",
    "ASSIGN_WORKER", "RESUME_WORKER",
})


@dataclasses.dataclass(frozen=True)
class IsolatedContinuationReservation:
    status: str
    reason: str
    idempotency_key: str | None = None
    browser_send_authorized: bool = False
    local_execution_authorized: bool = False
    committed_for_review: bool = False


def reserve_verified_continuation_for_review(
    database: pathlib.Path,
    *,
    allowed_experiments_root: pathlib.Path,
    request: ContinuationRequest,
    observation: SessionObservation,
    policy: AdmissionPolicy,
    samples: Sequence[TurnSample],
    receipts: Sequence[HostTerminalReceipt] | None,
    host_attestation_key: bytes | None,
    expected_intent_id: str,
    now_monotonic_ms: int,
) -> IsolatedContinuationReservation:
    """Return a review-only durable candidate, NEVER permission to send.

    Authoritative idempotency state is read inside the same BEGIN IMMEDIATE
    transaction which atomically reserves the receipt, scope cursor and
    continuation key. No caller-provided snapshot can bypass that transaction.
    """
    def reject(reason: str, status: str = "BLOCKED", key: str | None = None):
        return IsolatedContinuationReservation(status, reason, key)

    if not _safe_db_path(database, allowed_experiments_root):
        return reject("EXPERIMENT_SCOPE_INVALID")
    if not isinstance(request, ContinuationRequest):
        return reject("CONTINUATION_REQUEST_REQUIRED")
    if not isinstance(observation, SessionObservation) or not isinstance(policy, AdmissionPolicy):
        return reject("SESSION_BINDING_REQUIRED")
    if request.decision_action not in _RESUMABLE:
        return reject("NON_RESUMABLE_ACTION")
    if not isinstance(expected_intent_id, str) or not expected_intent_id.strip():
        return reject("INTENT_ID_INVALID")
    if not isinstance(receipts, (tuple, list)) or len(receipts) != 2:
        return reject("TWO_TERMINAL_RECEIPTS_REQUIRED")
    a, b = receipts
    if not isinstance(a, HostTerminalReceipt) or not isinstance(b, HostTerminalReceipt):
        return reject("HOST_RECEIPT_SHAPE_INVALID")
    canonical_url = _canonical_conversation_url(policy.expected_conversation_url)
    if canonical_url is None:
        return reject("EXPECTED_CONVERSATION_URL_INVALID")

    scope_key = _sha_json({
        "session_id": observation.session_id,
        "conversation_url": canonical_url,
        "binding_generation": policy.expected_generation,
    })
    event_key = _sha_json({
        "scope_key": scope_key, "event_id": b.event_id,
        "intent_id": expected_intent_id,
    })
    # Reject forged/stale/unusable completion before even creating the
    # isolated SQLite file. This is a preliminary integrity gate ONLY.
    # Queue membership is rechecked authoritatively under BEGIN IMMEDIATE.
    preliminary = plan_with_verified_turn(
        request, observation, policy, samples,
        expected_intent_id=expected_intent_id,
        now_monotonic_ms=now_monotonic_ms,
        already_queued=(),
        host_receipts=receipts,
        host_attestation_key=host_attestation_key,
    )
    if preliminary.status != "READY_FOR_GATED_ADAPTER":
        return reject("CONTINUATION_NOT_VERIFIED:" + preliminary.reason)
    receipt_digest = _sha_json(dataclasses.asdict(b))

    try:
        with contextlib.closing(sqlite3.connect(
            str(database), timeout=5, isolation_level=None
        )) as db:
            # Schema creation is confined to the verified isolated r2-* path.
            db.executescript(_SCHEMA + _EXTRA_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                # The persisted queue, not the model or caller, decides replay.
                keys = {row[0] for row in db.execute(
                    "SELECT idempotency_key FROM host_continuation_candidates"
                )}
                candidate = plan_with_verified_turn(
                    request, observation, policy, samples,
                    expected_intent_id=expected_intent_id,
                    now_monotonic_ms=now_monotonic_ms,
                    already_queued=keys,
                    host_receipts=receipts,
                    host_attestation_key=host_attestation_key,
                )
                if candidate.status == "ALREADY_QUEUED":
                    db.rollback()
                    return reject("IDEMPOTENCY_KEY_EXISTS", "ALREADY_QUEUED", candidate.idempotency_key)
                if candidate.status != "READY_FOR_GATED_ADAPTER":
                    db.rollback()
                    return reject("CONTINUATION_NOT_VERIFIED:" + candidate.reason)
                if not candidate.idempotency_key or not candidate.completion_proof_sha256:
                    db.rollback()
                    return reject("CONTINUATION_IDENTITY_MISSING")
                if db.execute(
                    "SELECT 1 FROM host_terminal_events WHERE event_key=?",
                    (event_key,),
                ).fetchone():
                    db.rollback()
                    return reject("TERMINAL_EVENT_ALREADY_RESERVED")
                high = db.execute(
                    "SELECT high_sequence FROM host_terminal_scope_cursors WHERE scope_key=?",
                    (scope_key,),
                ).fetchone()
                if high is not None and b.sequence <= high[0]:
                    db.rollback()
                    return reject("NON_MONOTONIC_HOST_SEQUENCE")

                db.execute(
                    "INSERT INTO host_terminal_events("
                    "event_key,scope_key,event_sequence,receipt_sha256"
                    ") VALUES(?,?,?,?)",
                    (event_key, scope_key, b.sequence, receipt_digest),
                )
                db.execute(
                    "INSERT INTO host_terminal_scope_cursors(scope_key,high_sequence)"
                    " VALUES(?,?) ON CONFLICT(scope_key)"
                    " DO UPDATE SET high_sequence=excluded.high_sequence",
                    (scope_key, b.sequence),
                )
                db.execute(
                    "INSERT INTO host_continuation_candidates("
                    "idempotency_key,event_key,project_id,decision_id,proof_sha256"
                    ") VALUES(?,?,?,?,?)",
                    (
                        candidate.idempotency_key, event_key, request.project_id,
                        request.decision_id, candidate.completion_proof_sha256,
                    ),
                )
                db.commit()
                return IsolatedContinuationReservation(
                    "RESERVED_FOR_REVIEW", "ATOMIC_NO_SEND_CONTINUATION_RECORDED",
                    candidate.idempotency_key, committed_for_review=True,
                )
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        return reject("EXPERIMENT_CONTINUATION_LEDGER_UNAVAILABLE")
