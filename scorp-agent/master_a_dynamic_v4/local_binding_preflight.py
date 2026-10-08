"""Windows local no-send binding preflight with authoritative SQLite inputs.

Reads the installed V3 Master registry, GUI Bridge health and R1 V4 SQLite in
READ ONLY mode before performing the existing Chrome Use inventory. Failure to
read authority is BLOCKED; there are no zero/False security defaults.
No browser navigation/selection/adoption/send, no scheduled tasks or writes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
from dataclasses import dataclass
from typing import Callable, Any

from .chrome_use_binding_audit import inspect_existing_chrome_use_session
from .session_admission import _canonical_conversation_url


_UNRESOLVED_STATES = (
    "BLOCKED_AMBIGUOUS", "FENCED_AMBIGUOUS", "MAY_HAVE_SUBMITTED",
    "CONFIRMED_SUBMITTED",
)


@dataclass(frozen=True)
class LocalBindingPreflight:
    status: str
    reason: str
    unresolved_intents: int | None = None
    rotation_conflict: bool | None = None
    live_sessions: int | None = None
    chatgpt_tabs: int | None = None
    matching_master_tabs: int | None = None
    browser_send_authorized: bool = False
    browser_adoption_authorized: bool = False


def inspect_local_binding(
    *,
    v3_project_root: pathlib.Path,
    gui_health_file: pathlib.Path,
    r1_state_db: pathlib.Path,
    chrome_use_executable: str,
    run: Callable[..., Any] | None = None,
) -> LocalBindingPreflight:
    """Never infer a clear ledger from a missing/unreadable DB or health file."""
    def blocked(reason: str, unresolved=None, rotation=None) -> LocalBindingPreflight:
        return LocalBindingPreflight("BLOCKED", reason, unresolved, rotation)

    try:
        root = pathlib.Path(v3_project_root)
        state = json.loads((root / "project-state.json").read_text(encoding="utf-8-sig"))
        reg = json.loads((root / "sessions-v3.json").read_text(encoding="utf-8-sig"))
        health = json.loads(pathlib.Path(gui_health_file).read_text(encoding="utf-8-sig"))
        if not isinstance(state, dict) or not isinstance(reg, dict) or not isinstance(health, dict):
            return blocked("AUTHORITY_METADATA_INVALID")
        if state.get("status") not in ("ACTIVE", "RUNNING"):
            return blocked("V3_PROJECT_NOT_ACTIVE")
        master = reg.get("master-conversation:" + str(state.get("project_id")))
        if not isinstance(master, dict):
            return blocked("V3_MASTER_BINDING_MISSING")
        url = _canonical_conversation_url(master.get("conversation_url", ""))
        if not url:
            return blocked("V3_MASTER_URL_INVALID")

        health_status = str(health.get("status") or "")
        error = str(health.get("error") or "")
        if health_status == "ERROR":
            if "MASTER_CONVERSATION_ROTATION_REQUIRED" not in error:
                return blocked("GUI_HEALTH_ERROR_UNCLASSIFIED")
            rotation = True
        elif health_status in ("IDLE", "HEALTHY", "READY"):
            rotation = False
        else:
            return blocked("GUI_HEALTH_STATE_UNVERIFIED")

        db = pathlib.Path(r1_state_db)
        if not db.is_file():
            return blocked("R1_SQLITE_MISSING", rotation=rotation)
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            con.execute("PRAGMA query_only=ON")
            row = con.execute("SELECT status FROM project_state LIMIT 1").fetchone()
            if not row or row[0] != "ACTIVE":
                return blocked("R1_PROJECT_NOT_ACTIVE", rotation=rotation)
            operator = con.execute("SELECT operator_state FROM operator_controls LIMIT 1").fetchone()
            if not operator or operator[0] not in ("RUNNING", "ACTIVE"):
                return blocked("R1_OPERATOR_NOT_ACTIVE", rotation=rotation)
            marks = ",".join("?" for _ in _UNRESOLVED_STATES)
            unresolved = int(con.execute(
                "SELECT COUNT(*) FROM action_intents WHERE state IN (" + marks + ")",
                _UNRESOLVED_STATES,
            ).fetchone()[0])
        finally:
            con.close()

        settings = dict(
            unresolved_master_intents=unresolved,
            rotation_conflict=rotation,
        )
        if run is not None:
            settings["run"] = run
        result = inspect_existing_chrome_use_session(
            chrome_use_executable, url, **settings,
        )
        return LocalBindingPreflight(
            status=result.status, reason=result.reason,
            unresolved_intents=unresolved, rotation_conflict=rotation,
            live_sessions=result.discovered_sessions,
            chatgpt_tabs=result.chatgpt_conversations,
            matching_master_tabs=result.matching_master_tabs,
        )
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return blocked("LOCAL_AUTHORITY_READ_FAILED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local GPT Master binding preflight")
    parser.add_argument("--v3-project-root", type=pathlib.Path, required=True)
    parser.add_argument("--gui-health-file", type=pathlib.Path, required=True)
    parser.add_argument("--r1-state-db", type=pathlib.Path, required=True)
    parser.add_argument("--chrome-use-executable", required=True)
    args = parser.parse_args(argv)
    result = inspect_local_binding(
        v3_project_root=args.v3_project_root,
        gui_health_file=args.gui_health_file,
        r1_state_db=args.r1_state_db,
        chrome_use_executable=args.chrome_use_executable,
    )
    from dataclasses import asdict
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
