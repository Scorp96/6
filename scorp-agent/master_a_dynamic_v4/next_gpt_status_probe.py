"""No-send next-GPT bootstrap status: fixed, safe Windows SCORP surfaces.

Read-only only: SQLite opens URI mode=ro, parses two existing JSON records,
does not invoke PowerShell, Chrome, Git, Task Scheduler, models, or the
production controller. Output is fixed-schema, contains NO URLs, prompts,
conversation IDs, process IDs, host owner or remote identifiers. Fail closed
on missing/corrupt inputs. This preflight is NOT browser authorization.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import pathlib
import re
import sqlite3
from typing import Any


_VERSION = "scorp.next-gpt-status-readonly/1"


def _utc(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except ValueError:
        return None


def _sql(path: pathlib.Path, reader) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "MISSING"}
    try:
        # Never call sqlite3.connect(path): that could CREATE a missing DB.
        uri = path.resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            return reader(conn)
    except (sqlite3.Error, ValueError, OSError, OverflowError, TypeError, KeyError, IndexError):
        return {"status": "UNREADABLE"}


def _observer(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT interval_minutes,last_reserved_ms,next_due_ms,pending_seq "
        "FROM observer_schedule WHERE id=1"
    ).fetchone()
    last = conn.execute(
        "SELECT seq,observed_ms,status,reason,changed "
        "FROM observer_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM observer_events").fetchone()[0]
    check = conn.execute("PRAGMA quick_check").fetchone()[0]
    if row is None or last is None:
        return {"status": "INCOMPLETE"}
    if (row[0] not in (15, 25) or not isinstance(row[1], int)
        or not isinstance(row[2], int)):
        return {"status": "INVALID"}
    return {
        "status": "OBSERVED" if check == "ok" else "INTEGRITY_FAILED",
        "db_integrity": "ok" if check == "ok" else "NOT_OK",
        "event_count": int(count),
        "last_event_seq": int(last[0]),
        "last_observed_utc": dt.datetime.fromtimestamp(
            last[1] / 1000, dt.timezone.utc
        ).isoformat(),
        "last_result": str(last[2]) if str(last[2]) in ("BLOCKED", "READONLY_MATCH_REVIEW_REQUIRED") else "UNCLASSIFIED",
        "last_reason": str(last[3]) if re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", str(last[3])) else "REDACTED",
        "last_changed": bool(last[4]),
        "due_interval_minutes": int(row[0]),
        "next_due_utc": dt.datetime.fromtimestamp(
            row[2] / 1000, dt.timezone.utc
        ).isoformat(),
        "reservation_pending": row[3] is not None,
    }


def _r1(conn: sqlite3.Connection) -> dict[str, Any]:
    project = conn.execute("SELECT status,phase,state_version FROM project_state LIMIT 1").fetchone()
    lease = conn.execute(
        "SELECT daemon_epoch,lease_status,heartbeat_at,lease_until "
        "FROM daemon_leases LIMIT 1"
    ).fetchone()
    sup = conn.execute(
        "SELECT recovery_count,consecutive_failures,circuit_state,last_failure_at "
        "FROM daemon_supervision LIMIT 1"
    ).fetchone()
    if project is None or lease is None or sup is None:
        return {"status": "INCOMPLETE"}
    return {
        "status": "READ_ONLY_OK",
        "project_status": str(project[0]) if str(project[0]) in ("ACTIVE", "PAUSED", "BLOCKED", "TERMINAL") else "OTHER",
        "phase": str(project[1]) if str(project[1]) in ("BOOTSTRAP", "RUNNING", "PAUSED", "TERMINAL") else "OTHER",
        "state_version": int(project[2]),
        "ambiguous_intents": int(conn.execute(
            "SELECT COUNT(*) FROM action_intents WHERE state='BLOCKED_AMBIGUOUS'"
        ).fetchone()[0]),
        "total_intents": int(conn.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0]),
        "browser_binding_count": int(conn.execute("SELECT COUNT(*) FROM browser_bindings").fetchone()[0]),
        "daemon_epoch": int(lease[0]),
        "daemon_lease_status": str(lease[1]) if lease[1] in ("ACTIVE", "RELEASED") else "OTHER",
        "daemon_lease_heartbeat_utc": _utc(lease[2]),
        "daemon_lease_expires_utc": _utc(lease[3]),
        "daemon_recovery_count": int(sup[0]),
        "daemon_consecutive_failures": int(sup[1]),
        "daemon_circuit": str(sup[2]) if sup[2] in ("CLOSED", "OPEN", "BACKOFF", "BLOCKED") else "OTHER",
        "daemon_last_failure_utc": _utc(sup[3]),
    }


def _json(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        # Cap input size to avoid parsing an untrusted multi-gigabyte log.
        if path.stat().st_size > 1024 * 1024:
            return None
        with path.open("r", encoding="utf-8-sig") as f:
            result = json.load(f)
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def observe(root: pathlib.Path, *, now_utc: dt.datetime | None = None) -> dict[str, Any]:
    if not isinstance(root, pathlib.Path):
        raise TypeError("SCORP_ROOT_REQUIRED")
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("UTC_CLOCK_REQUIRED")
    root = root.resolve()
    r1 = _sql(root / "runtime-v4/active/state.sqlite3", _r1)
    observer = _sql(
        root / "experiments/r2-observer-state-20261009/scorp-readonly-observer.sqlite3",
        _observer,
    )
    gui = _json(root / "chatgpt-gui-bridge-state/health.json")
    driver = _json(root / "state-v3/active/chrome-use-driver-v3.json")
    gui_result = {
        "status": str(gui.get("status")) if gui and gui.get("status") in ("HEALTHY", "ERROR", "BLOCKED") else "UNAVAILABLE",
        "master_rotation_conflict": bool(gui and "MASTER_CONVERSATION_ROTATION_REQUIRED" in str(gui.get("error") or "")),
    }
    sessions = driver.get("sessions") if driver else None
    driver_result = {
        "status": "READ_ONLY_OK" if isinstance(sessions, dict) else "UNAVAILABLE",
        "physical_sessions_count": len(sessions) if isinstance(sessions, dict) else None,
    }
    blockers = []
    if r1.get("status") != "READ_ONLY_OK":
        blockers.append("R1_AUTHORITY_UNAVAILABLE")
    elif r1["ambiguous_intents"]:
        blockers.append("AMBIGUOUS_SUBMIT_UNRESOLVED")
    if driver_result["status"] != "READ_ONLY_OK" or driver_result["physical_sessions_count"] == 0:
        blockers.append("PHYSICAL_GPT_SESSION_UNVERIFIED")
    if gui_result["status"] != "HEALTHY":
        blockers.append("GUI_BRIDGE_NOT_HEALTHY")
    if gui_result["master_rotation_conflict"]:
        blockers.append("MASTER_CONVERSATION_ROTATION_REQUIRED")
    if observer.get("status") != "OBSERVED":
        blockers.append("ISOLATED_OBSERVER_STATE_UNVERIFIED")
    return {
        "protocol_version": _VERSION,
        "observed_utc": now.astimezone(dt.timezone.utc).isoformat(),
        "r1": r1,
        "observer_15m": observer,
        "gui_bridge": gui_result,
        "v3_driver": driver_result,
        "blockers": sorted(set(blockers)),
        "browser_send_authorized": False,
        "local_execution_authorized": False,
        "production_writes": "NONE",
        "model_calls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SCORP no-send next-GPT read-only preflight")
    parser.add_argument("--scorp-root", type=pathlib.Path, default=pathlib.Path(r"C:\ScorpAgent"))
    args = parser.parse_args(argv)
    print(json.dumps(observe(args.scorp_root), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
