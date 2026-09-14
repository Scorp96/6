from __future__ import annotations

import sqlite3
from typing import Any

from .state_store import StateStore, StoreInvariantError


def _rows(conn: sqlite3.Connection, table: str, project_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        f"SELECT * FROM {table} WHERE project_id=? ORDER BY rowid", (project_id,)
    ).fetchall()]


def export_read_only(store: StateStore, project_id: str) -> dict[str, Any]:
    """Return a non-authoritative snapshot using SQLite's enforced read-only mode."""
    uri = f"file:{store.path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        contract = conn.execute(
            "SELECT * FROM contracts WHERE project_id=?", (project_id,)
        ).fetchone()
        state = conn.execute(
            "SELECT * FROM project_state WHERE project_id=?", (project_id,)
        ).fetchone()
        if contract is None or state is None:
            raise StoreInvariantError("PROJECT_NOT_FOUND")
        return {
            "authority": "READ_ONLY_EXPORT",
            "project_id": project_id,
            "contract": dict(contract),
            "project_state": dict(state),
            "tasks": _rows(conn, "task_nodes", project_id),
            "transitions": _rows(conn, "transitions", project_id),
            "events": _rows(conn, "events", project_id),
            "action_intents": _rows(conn, "action_intents", project_id),
            "evidence_receipts": _rows(conn, "evidence_receipts", project_id),
        }
    finally:
        conn.close()
