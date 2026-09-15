"""Explicit, fail-closed migration of a legacy SQLite snapshot.

The source is opened read-only and the destination must not exist.  This
module deliberately does not open the source with :class:`StateStore`: doing
so would allow the normal schema bootstrap to mutate an old database.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import sqlite3
from collections.abc import Sequence

from .models import canonical_json
from .state_store import StateStore, StoreInvariantError, _REQUIRED_SCHEMA_TABLES, utc_now


_COPY_ORDER = (
    "contracts",
    "project_state",
    "daemon_leases",
    "daemon_supervision",
    "master_sessions",
    "task_nodes",
    "task_dependencies",
    "assignments",
    "leases",
    "events",
    "transitions",
    "action_intents",
    "outbox",
    "browser_bindings",
    "candidate_results",
    "evidence_receipts",
    "review_deadlines",
    "review_findings",
    "release_candidates",
    "operator_controls",
    "runtime_observations",
    "runtime_command_receipts",
)


@dataclasses.dataclass(frozen=True)
class SqliteMigrationReceipt:
    snapshot_id: str
    source_path: str
    destination_path: str
    source_sha256: str
    source_size: int
    source_mtime_ns: int
    status: str
    conflicts: tuple[str, ...]


def _within(path: pathlib.Path, roots: tuple[pathlib.Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def _required_missing(dest_info: list[sqlite3.Row], values: dict[str, object]) -> list[str]:
    missing: list[str] = []
    for row in dest_info:
        name = str(row[1])
        if name in values or int(row[5]):
            continue
        if int(row[3]) and row[4] is None:
            missing.append(name)
    return missing


def _mapped_values(
    table: str,
    source_columns: list[str],
    destination_columns: list[str],
    row: sqlite3.Row,
    snapshot_id: str,
) -> dict[str, object]:
    values = {name: row[name] for name in source_columns if name in destination_columns}
    if table == "contracts" and "imported_snapshot_id" in destination_columns:
        values["imported_snapshot_id"] = snapshot_id
    if table == "task_nodes" and "task_context_json" not in values:
        values["task_context_json"] = "{}"
    if table == "assignments" and "base_state_version" not in values:
        values["base_state_version"] = 0
    if table == "leases" and "heartbeat_at" not in values:
        values["heartbeat_at"] = values.get("acquired_at")
    if table == "daemon_leases" and "lease_status" not in values:
        values["lease_status"] = "ACTIVE"
    if table == "runtime_observations" and "last_heartbeat_at" not in values:
        values["last_heartbeat_at"] = values.get("last_observed_at")
    return values


def migrate_sqlite_snapshot(
    source_path: str | pathlib.Path,
    destination_path: str | pathlib.Path,
    *,
    allowed_roots: Sequence[str | pathlib.Path],
) -> SqliteMigrationReceipt:
    """Copy a supported legacy SQLite snapshot into a new V4 database.

    The operation is intentionally one-way and explicit.  It never updates
    ``source_path`` and refuses to overwrite ``destination_path``.  A source
    without a valid contracts table is recorded as ``BLOCKED`` and cannot
    become an active authority.
    """

    roots = tuple(pathlib.Path(root).resolve() for root in allowed_roots)
    if not roots:
        raise StoreInvariantError("ALLOWED_ROOTS_EMPTY")
    source = pathlib.Path(source_path).resolve(strict=True)
    destination = pathlib.Path(destination_path).resolve()
    if not _within(source, roots) or not source.is_file():
        raise StoreInvariantError("MIGRATION_SOURCE_OUTSIDE_ALLOWLIST")
    if not _within(destination, roots):
        raise StoreInvariantError("MIGRATION_DESTINATION_OUTSIDE_ALLOWLIST")
    if source == destination:
        raise StoreInvariantError("MIGRATION_SOURCE_DESTINATION_SAME")
    if destination.exists():
        raise StoreInvariantError("MIGRATION_DESTINATION_EXISTS")

    raw = source.read_bytes()
    source_stat = source.stat()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    snapshot_id = f"sqlite-snapshot-{source_sha256}"
    conflicts: list[str] = []
    source_tables: set[str] = set()
    source_rows: dict[str, list[sqlite3.Row]] = {}
    try:
        uri = "file:" + source.as_posix() + "?mode=ro"
        source_conn = sqlite3.connect(uri, uri=True)
        source_conn.row_factory = sqlite3.Row
        try:
            check = str(source_conn.execute("PRAGMA integrity_check").fetchone()[0])
            if check.lower() != "ok":
                raise StoreInvariantError("MIGRATION_SOURCE_INTEGRITY_FAILED")
            source_tables = {
                str(row[0])
                for row in source_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            unsupported = sorted(source_tables - (set(_REQUIRED_SCHEMA_TABLES) | {"schema_migrations"}))
            conflicts.extend(f"UNSUPPORTED_TABLE:{name}" for name in unsupported)
            for table in _COPY_ORDER:
                if table in source_tables:
                    source_rows[table] = source_conn.execute(
                        f'SELECT * FROM "{table}"'
                    ).fetchall()
            if "contracts" not in source_tables or not source_rows.get("contracts"):
                conflicts.append("MISSING_ROOT_CONTRACT")
        finally:
            source_conn.close()
    except sqlite3.DatabaseError as exc:
        raise StoreInvariantError("MIGRATION_SOURCE_UNREADABLE") from exc

    # Detect a source write racing this read before creating a destination.
    if source.read_bytes() != raw or source.stat().st_mtime_ns != source_stat.st_mtime_ns:
        raise StoreInvariantError("MIGRATION_SOURCE_CHANGED")

    destination.parent.mkdir(parents=True, exist_ok=True)
    store = StateStore(destination, allowed_roots=roots)
    try:
        status = "IMPORTED_SNAPSHOT" if not conflicts else "BLOCKED"
        with store._transaction() as conn:
            conn.execute(
                """
                INSERT INTO imported_snapshots(
                    snapshot_id,source_path,source_sha256,source_size,source_mtime_ns,
                    schema_name,status,conflicts_json,imported_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id,
                    str(source),
                    source_sha256,
                    len(raw),
                    source_stat.st_mtime_ns,
                    "sqlite-legacy",
                    status,
                    canonical_json(conflicts),
                    utc_now(),
                ),
            )
            if status == "IMPORTED_SNAPSHOT":
                for table in _COPY_ORDER:
                    rows = source_rows.get(table, [])
                    if not rows:
                        continue
                    source_columns = list(rows[0].keys())
                    destination_info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    destination_columns = [str(row[1]) for row in destination_info]
                    for row in rows:
                        values = _mapped_values(
                            table, source_columns, destination_columns, row, snapshot_id
                        )
                        missing = _required_missing(destination_info, values)
                        if missing:
                            raise StoreInvariantError(
                                f"MIGRATION_UNMAPPED_REQUIRED_COLUMNS:{table}:{','.join(missing)}"
                            )
                        names = list(values)
                        placeholders = ",".join("?" for _ in names)
                        conn.execute(
                            f'INSERT INTO "{table}" ({",".join(names)}) VALUES ({placeholders})',
                            tuple(values[name] for name in names),
                        )
        return SqliteMigrationReceipt(
            snapshot_id=snapshot_id,
            source_path=str(source),
            destination_path=str(destination),
            source_sha256=source_sha256,
            source_size=len(raw),
            source_mtime_ns=source_stat.st_mtime_ns,
            status=status,
            conflicts=tuple(dict.fromkeys(conflicts)),
        )
    finally:
        store.close()
