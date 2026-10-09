"""Isolated, privacy-minimal 15/25-minute local browser observation journal.

The loopback sampler costs ZERO model calls. This durable journal records only
coarse session/tab counts and a fixed outcome class; never a page, prompt, URL,
session name, cookie, Chrome target, actual assistant turn or login identity.

This module cannot send messages, wake ChatGPT, start workers, or mark a
TURN_FINAL_CONFIRMED. It is NOT an authentication or terminal event issuer.

SQLite BEGIN IMMEDIATE serializes concurrent samplers. Cadence is a MINIMUM
spacing, never a wake signal. Unexpected clock rewind fails closed; a skipped
or interrupted observation does not grant permission to retry a browser send.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import stat

from chrome_use_loopback_observer_v4 import LocalChromeReadObservation
from isolated_chrome_namespace_admission_v4 import _CANARY


@dataclass(frozen=True)
class LocalPollJournalReceipt:
    status: str
    reason: str
    sample_written: bool = False
    category: str = "NONE"
    cadence_minutes: int = 0
    local_model_calls: int = 0
    native_final_event_verified: bool = False
    browser_send_authorized: bool = False
    worker_wake_authorized: bool = False
    production_cutover_authorized: bool = False


def _safe_observer_db(path: Path, isolated_root: Path) -> bool:
    if not isinstance(path, Path) or not isinstance(isolated_root, Path):
        return False
    try:
        root = isolated_root.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
        if (
            parent == root or root not in parent.parents
            or not parent.name.startswith("r2-loopback-poll-")
            or path.name != "local-poll-journal.sqlite3"
            or path.is_symlink()
        ):
            return False
        if path.exists():
            entry = path.stat()
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
                return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def commit_one_nonterminal_local_sample(
    observation: LocalChromeReadObservation,
    *,
    isolated_namespace: str,
    database: Path,
    allowed_isolated_root: Path,
    cadence_minutes: int = 15,
    observed_utc: datetime | None = None,
) -> LocalPollJournalReceipt:
    def result(status, reason, written=False, category="NONE"):
        return LocalPollJournalReceipt(
            status, reason, written, category,
            cadence_minutes if type(cadence_minutes) is int else 0,
        )

    if (
        not isinstance(isolated_namespace, str)
        or _CANARY.fullmatch(isolated_namespace) is None
        or cadence_minutes not in (15, 25)
        or type(cadence_minutes) is not int
        or not isinstance(observation, LocalChromeReadObservation)
    ):
        return result("BLOCKED", "LOCAL_POLL_CONTRACT_INVALID")
    if not _safe_observer_db(database, allowed_isolated_root):
        return result("BLOCKED", "ISOLATED_POLL_DB_SCOPE_INVALID")
    moment = observed_utc if observed_utc is not None else datetime.now(timezone.utc)
    if (
        not isinstance(moment, datetime) or moment.tzinfo is None
        or moment.utcoffset() is None
    ):
        return result("BLOCKED", "UTC_SAMPLE_TIMESTAMP_INVALID")
    try:
        epoch = int(moment.astimezone(timezone.utc).timestamp())
    except (ValueError, OverflowError, OSError):
        return result("BLOCKED", "UTC_SAMPLE_TIMESTAMP_INVALID")
    if epoch < 1_577_836_800 or epoch > 4_102_444_800:
        return result("BLOCKED", "UTC_SAMPLE_TIMESTAMP_OUTSIDE_SAFE_RANGE")
    # Only genuine count-shaped observations can be journaled as healthy.
    # No source status or human-derived completion text enters SQLite.
    valid = (
        observation.status == "LOCAL_STATE_OBSERVED_UNATTESTED"
        and observation.all_three_endpoints_readable is True
        and observation.status_endpoint_readable is True
        and type(observation.session_count) is int
        and type(observation.tab_count) is int
        and 0 <= observation.session_count <= 256
        and 0 <= observation.tab_count <= 256
        and observation.local_model_calls == 0
        and not observation.native_final_event_verified
        and not observation.browser_send_authorized
        and not observation.local_execution_authorized
        and not observation.authentication_verified
    )
    if not valid:
        return result("BLOCKED", "LOCAL_SOURCE_UNVERIFIED_NO_JOURNAL")
    fingerprint = hashlib.sha256(
        ("scorp-r2-loopback-observe-v1:" + isolated_namespace).encode("utf-8"),
    ).hexdigest()
    try:
        with contextlib.closing(sqlite3.connect(
            str(database), isolation_level=None, timeout=3,
        )) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS local_poll_sample (
                        session_digest TEXT NOT NULL,
                        observed_epoch INTEGER NOT NULL,
                        cadence_minutes INTEGER NOT NULL,
                        session_count INTEGER NOT NULL,
                        tab_count INTEGER NOT NULL,
                        class TEXT NOT NULL,
                        PRIMARY KEY(session_digest,observed_epoch)
                    )
                """)
                previous = db.execute(
                    "SELECT observed_epoch,session_count,tab_count,cadence_minutes "
                    "FROM local_poll_sample WHERE session_digest=? "
                    "ORDER BY observed_epoch DESC LIMIT 1",
                    (fingerprint,),
                ).fetchone()
                if previous is not None and epoch <= previous[0]:
                    db.rollback()
                    return result("BLOCKED", "CLOCK_REWIND_OR_DUPLICATE")
                if previous is not None and epoch - previous[0] < max(cadence_minutes, previous[3]) * 60:
                    db.rollback()
                    return result("BLOCKED", "POLL_CADENCE_NOT_ELAPSED")
                if previous is None:
                    category = "FIRST_LOCAL_STATE_UNATTESTED"
                elif observation.session_count != previous[1] or observation.tab_count != previous[2]:
                    category = "COUNTS_CHANGED_UNATTESTED"
                else:
                    category = "COUNTS_STABLE_UNATTESTED"
                db.execute(
                    "INSERT INTO local_poll_sample("
                    "session_digest,observed_epoch,cadence_minutes,"
                    "session_count,tab_count,class) VALUES (?,?,?,?,?,?)",
                    (
                        fingerprint, epoch, cadence_minutes,
                        observation.session_count, observation.tab_count,
                        category,
                    ),
                )
                db.commit()
                return result(
                    "LOCAL_NONTERMINAL_SAMPLE_COMMITTED",
                    "OBSERVE_ONLY_NO_GPT_DONE_OR_WAKE_AUTHORITY",
                    True, category,
                )
            except BaseException:
                db.rollback()
                raise
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return result("BLOCKED", "DURABLE_LOCAL_POLL_UNAVAILABLE")
