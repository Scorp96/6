"""Deterministic, token-free, no-send SCORP GUI observer.

One invocation = at most one read-only binding preflight. No sleeps, GPT calls,
Codex calls, browser send, tab adoption or task scheduling. An *external*
Windows scheduler may invoke this CLI frequently; this module persists 15/25
minute due times in an ISOLATED SQLite file, with a crash-safe reservation.

Important: a pending reservation after a crash deliberately blocks future
ticks until an operator reviews that isolated event. The database is never
stored under the V3/R1 production runtime path.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import pathlib
import re
import sqlite3
import time
from typing import Callable, Any

from .local_binding_preflight import LocalBindingPreflight, inspect_local_binding


_SCHEMA = """
CREATE TABLE IF NOT EXISTS observer_schedule(
  id INTEGER PRIMARY KEY CHECK(id=1),
  interval_minutes INTEGER NOT NULL CHECK(interval_minutes IN (15,25)),
  last_reserved_ms INTEGER NOT NULL,
  next_due_ms INTEGER NOT NULL,
  pending_seq INTEGER,
  next_seq INTEGER NOT NULL,
  last_fingerprint TEXT
);
CREATE TABLE IF NOT EXISTS observer_events(
  seq INTEGER PRIMARY KEY,
  observed_ms INTEGER NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  changed INTEGER NOT NULL CHECK(changed IN (0,1))
);
"""
_SAFE_REASON = re.compile(r"^[A-Z][A-Z0-9_]{1,95}$")
_ALLOWED_STATUSES = {"BLOCKED", "READONLY_MATCH_REVIEW_REQUIRED"}


@dataclasses.dataclass(frozen=True)
class TickOutcome:
    status: str
    reason: str
    interval_minutes: int
    next_due_ms: int | None = None
    event_seq: int | None = None
    changed: bool = False
    browser_send_authorized: bool = False
    browser_adoption_authorized: bool = False


def run_tick(
    database: pathlib.Path,
    *,
    interval_minutes: int,
    now_ms: int,
    inspect: Callable[[], LocalBindingPreflight],
) -> TickOutcome:
    """Reserve atomically before observation; never retry a crash-ambiguous tick."""
    if type(interval_minutes) is not int or interval_minutes not in (15,25):
        raise ValueError("OBSERVER_INTERVAL_MUST_BE_15_OR_25")
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("OBSERVER_WALL_CLOCK_INVALID")
    if not callable(inspect):
        raise TypeError("OBSERVER_INSPECT_REQUIRED")
    db = pathlib.Path(database)
    if not db.parent.is_dir():
        raise ValueError("OBSERVER_PARENT_DIRECTORY_MISSING")

    # Must be pre-existing, never a production DB. The CLI validates the
    # actual Windows isolation boundary; programmatic callers own their path.
    with contextlib.closing(sqlite3.connect(db,timeout=4,isolation_level=None)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT interval_minutes,last_reserved_ms,next_due_ms,"
                "pending_seq,next_seq,last_fingerprint "
                "FROM observer_schedule WHERE id=1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO observer_schedule VALUES(1,?,?,?,?,?,?)",
                    (interval_minutes,-1,now_ms,None,1,None),
                )
                row = (interval_minutes,-1,now_ms,None,1,None)
            minutes,last_tick,next_due,pending,seq,previous = row
            if minutes != interval_minutes:
                result = TickOutcome("BLOCKED","INTERVAL_CONFIGURATION_CONFLICT",interval_minutes,next_due)
            elif pending is not None:
                result = TickOutcome("BLOCKED","INCOMPLETE_PREVIOUS_TICK",interval_minutes,next_due)
            elif now_ms < last_tick:
                result = TickOutcome("BLOCKED","WALL_CLOCK_REGRESSED",interval_minutes,next_due)
            elif now_ms < next_due:
                result = TickOutcome("SKIPPED","NOT_DUE",interval_minutes,next_due)
            else:
                conn.execute(
                    "UPDATE observer_schedule SET last_reserved_ms=?,next_due_ms=?,"
                    "pending_seq=?,next_seq=? WHERE id=1",
                    (now_ms,now_ms+minutes*60*1000,seq,seq+1),
                )
                result = None
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        if result is not None:
            return result

        # No open write transaction is held during CLI/browser reads.
        try:
            observed = inspect()
            if not isinstance(observed, LocalBindingPreflight):
                raise TypeError("OBSERVER_RESULT_INVALID")
            status = observed.status if observed.status in _ALLOWED_STATUSES else "BLOCKED"
            reason = observed.reason
            if not isinstance(reason,str) or not _SAFE_REASON.fullmatch(reason):
                reason = "UNCLASSIFIED_OBSERVATION"
                status = "BLOCKED"
            if observed.browser_send_authorized or observed.browser_adoption_authorized:
                status, reason = "BLOCKED", "UNEXPECTED_BROWSER_AUTHORITY"
            # Do not persist conversation URLs, model text, prompts, session
            # identifiers, raw error strings, auth tokens or process metadata.
            safe = {
                "status":status,
                "reason":reason,
                "unresolved":observed.unresolved_intents if type(observed.unresolved_intents) is int else None,
                "rotation":observed.rotation_conflict if type(observed.rotation_conflict) is bool else None,
                "live_sessions":observed.live_sessions if type(observed.live_sessions) is int else None,
                "chatgpt_tabs":observed.chatgpt_tabs if type(observed.chatgpt_tabs) is int else None,
                "matching_master_tabs":observed.matching_master_tabs if type(observed.matching_master_tabs) is int else None,
            }
        except Exception:
            safe = {"status":"BLOCKED","reason":"OBSERVER_READ_FAILED"}
        fingerprint=hashlib.sha256(
            json.dumps(safe,sort_keys=True,separators=(",",":")).encode()
        ).hexdigest()
        conn.execute("BEGIN IMMEDIATE")
        try:
            check=conn.execute(
                "SELECT pending_seq,last_fingerprint,next_due_ms "
                "FROM observer_schedule WHERE id=1"
            ).fetchone()
            if check is None or check[0] != seq:
                conn.rollback()
                return TickOutcome("BLOCKED","OBSERVER_RESERVATION_FENCED",interval_minutes)
            changed=fingerprint!=check[1]
            conn.execute(
                "INSERT INTO observer_events(seq,observed_ms,status,reason,fingerprint,changed) "
                "VALUES(?,?,?,?,?,?)",
                (seq,now_ms,safe["status"],safe["reason"],fingerprint,int(changed)),
            )
            conn.execute(
                "UPDATE observer_schedule SET pending_seq=NULL,last_fingerprint=? WHERE id=1",
                (fingerprint,),
            )
            # Bounded history, preserving deterministic order. No transcript.
            conn.execute(
                "DELETE FROM observer_events WHERE seq < ?",(max(0,seq-499),)
            )
            conn.commit()
            return TickOutcome(
                "OBSERVED", safe["reason"],interval_minutes,check[2],seq,changed,
            )
        except BaseException:
            conn.rollback()
            raise


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="No-send SCORP observer, one tick")
    parser.add_argument("--workspace",required=True,type=pathlib.Path)
    parser.add_argument("--interval-minutes",required=True,type=int,choices=(15,25))
    parser.add_argument("--v3-project-root",required=True,type=pathlib.Path)
    parser.add_argument("--gui-health-file",required=True,type=pathlib.Path)
    parser.add_argument("--r1-state-db",required=True,type=pathlib.Path)
    parser.add_argument("--chrome-use-executable",required=True)
    args=parser.parse_args(argv)
    # No production directory can ever be chosen by this executable.
    permitted=pathlib.Path(r"C:\ScorpAgent\experiments").resolve()
    workspace=args.workspace.resolve()
    if workspace==permitted or permitted not in workspace.parents:
        parser.error("WORKSPACE_OUTSIDE_ISOLATED_EXPERIMENTS")
    if not workspace.is_dir():
        parser.error("WORKSPACE_MUST_ALREADY_EXIST")
    def observe() -> LocalBindingPreflight:
        return inspect_local_binding(
            v3_project_root=args.v3_project_root,
            gui_health_file=args.gui_health_file,
            r1_state_db=args.r1_state_db,
            chrome_use_executable=args.chrome_use_executable,
        )
    outcome=run_tick(
        workspace/"scorp-readonly-observer.sqlite3",
        interval_minutes=args.interval_minutes,
        now_ms=time.time_ns()//1_000_000,
        inspect=observe,
    )
    print(json.dumps(dataclasses.asdict(outcome),sort_keys=True,separators=(",",":")))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
