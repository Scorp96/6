"""Read-only and redacted preflight for a Windows GUI Bridge handoff.

This utility never opens Chrome, writes SQLite, modifies task state, reads
the raw messages or prints conversation URLs. Use it to classify legacy V3
migration blockers before considering a new isolated GPT1..N R2 canary.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
from collections import Counter
from typing import Any


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value=json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _short_sha(value: Any) -> str | None:
    text=str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else None


def inspect_gui_preflight(
    v3_project_root: pathlib.Path,
    bridge_health_file: pathlib.Path,
    r1_state_db: pathlib.Path,
) -> dict[str, Any]:
    """A bounded metadata-only diagnostic; all authority remains unchanged."""
    v3=pathlib.Path(v3_project_root)
    state=_load_json(v3/"project-state.json")
    registry=_load_json(v3/"sessions-v3.json")
    ledger=_load_json(v3/"master-worker-v3-ledger.json")
    health=_load_json(pathlib.Path(bridge_health_file))
    project=state.get("project_id")
    master=registry.get("master-conversation:"+str(project))
    canonical_url=(master or {}).get("conversation_url") if isinstance(master, dict) else None
    session_rows=[
        value for value in registry.values()
        if isinstance(value,dict)
        and value.get("project_id")==project
        and value.get("actor_kind")=="MASTER"
        and "session_id" in value
    ]
    states=Counter()
    ambiguous_actors=Counter()
    ambiguous_without_url=0
    for value in ledger.values():
        if not isinstance(value,dict):
            continue
        kind=str(value.get("state") or "UNKNOWN")
        states[kind]+=1
        if kind=="GUI_AMBIGUOUS":
            ambiguous_actors[str(value.get("actor_kind") or "UNKNOWN")]+=1
            if not value.get("conversation_url"):
                ambiguous_without_url+=1
    report={
        "protocol_version":"scorp.gui-preflight-readonly/1",
        "operational_authority":"UNCHANGED",
        "browser_send_authorized":False,
        "project_status":str(state.get("status") or "UNKNOWN"),
        "master_binding_exists":isinstance(master,dict),
        "master_url_sha256_prefix":_short_sha(canonical_url),
        "master_sessions":len(session_rows),
        "master_sessions_consistent":all(
            row.get("conversation_url")==canonical_url for row in session_rows
        ) if canonical_url else False,
        "v3_ledger_states":dict(states),
        "v3_ambiguous_actors":dict(ambiguous_actors),
        "v3_ambiguous_without_url":ambiguous_without_url,
        "bridge_health_status":str(health.get("status") or "UNKNOWN"),
        "bridge_rotation_blocked":str(health.get("error") or "").strip()
            =="MASTER_CONVERSATION_ROTATION_REQUIRED",
        "r1_sqlite_access":"NOT_FOUND",
        "r1_ambiguous_intent_count":None,
    }

    db=pathlib.Path(r1_state_db)
    if db.is_file():
        try:
            connection=sqlite3.connect(db.resolve().as_uri()+"?mode=ro",uri=True,timeout=2)
            try:
                connection.execute("PRAGMA query_only=ON")
                row=connection.execute(
                    "SELECT COUNT(*) FROM action_intents WHERE state='BLOCKED_AMBIGUOUS'"
                ).fetchone()
                report["r1_ambiguous_intent_count"]=int(row[0]) if row else None
                report["r1_sqlite_access"]="READ_ONLY"
            finally:
                connection.close()
        except (sqlite3.Error, OSError, ValueError):
            report["r1_sqlite_access"]="READ_ERROR"

    if report["bridge_rotation_blocked"]:
        report["next_action"]="BLOCKED_ROTATION_RECONCILIATION"
    elif report["r1_ambiguous_intent_count"]:
        report["next_action"]="BLOCKED_AMBIGUOUS_INTENT"
    elif not report["master_binding_exists"]:
        report["next_action"]="REQUIRES_PHYSICAL_BINDING_VERIFICATION"
    else:
        report["next_action"]="OBSERVE_ONLY_UNVERIFIED"
    return report
