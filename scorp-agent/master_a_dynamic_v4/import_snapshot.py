from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
from collections.abc import Mapping

from .models import canonical_json, sha256_json
from .state_store import StateStore, StoreInvariantError, utc_now


@dataclasses.dataclass(frozen=True)
class ImportReceipt:
    snapshot_id: str
    source_path: str
    source_sha256: str
    source_size: int
    status: str
    conflicts: tuple[str, ...]


def _within(path: pathlib.Path, roots: tuple[pathlib.Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _from_row(row) -> ImportReceipt:
    return ImportReceipt(
        snapshot_id=str(row["snapshot_id"]),
        source_path=str(row["source_path"]),
        source_sha256=str(row["source_sha256"]),
        source_size=int(row["source_size"]),
        status=str(row["status"]),
        conflicts=tuple(json.loads(str(row["conflicts_json"]))),
    )


def import_snapshot(path: str | pathlib.Path, store: StateStore) -> ImportReceipt:
    source = pathlib.Path(path).resolve(strict=True)
    if not source.is_file() or not _within(source, store.allowed_roots):
        raise StoreInvariantError("IMPORT_SOURCE_OUTSIDE_ALLOWLIST")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    snapshot_id = f"snapshot-{digest}"
    conflicts: list[str] = []
    value: object
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        value = None
        conflicts.append("INVALID_JSON")
    if not isinstance(value, Mapping):
        conflicts.append("SNAPSHOT_NOT_OBJECT")
        value = {}
    project_id = str(value.get("project_id") or "").strip()
    root_contract = value.get("root_contract")
    acceptance_contract = value.get("acceptance_contract")
    if not project_id:
        conflicts.append("MISSING_PROJECT_ID")
    if not isinstance(root_contract, Mapping) or not root_contract:
        conflicts.append("MISSING_ROOT_CONTRACT")
    if not isinstance(acceptance_contract, Mapping) or not acceptance_contract:
        conflicts.append("MISSING_ACCEPTANCE_CONTRACT")

    root_value = dict(root_contract) if isinstance(root_contract, Mapping) else {}
    acceptance_value = dict(acceptance_contract) if isinstance(acceptance_contract, Mapping) else {}
    root_hash = sha256_json(root_value) if root_value else ""
    acceptance_hash = sha256_json(acceptance_value) if acceptance_value else ""
    contract_hash = sha256_json({
        "root_contract_sha256": root_hash,
        "acceptance_contract_sha256": acceptance_hash,
    }) if root_hash and acceptance_hash else ""

    with store._transaction() as conn:
        existing_snapshot = conn.execute(
            "SELECT * FROM imported_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if existing_snapshot is not None:
            if str(existing_snapshot["source_path"]) != str(source):
                raise StoreInvariantError("SNAPSHOT_HASH_PATH_CONFLICT")
            return _from_row(existing_snapshot)

        existing_contract = None
        if project_id:
            existing_contract = conn.execute(
                "SELECT * FROM contracts WHERE project_id=?", (project_id,)
            ).fetchone()
        if existing_contract is not None and str(existing_contract["contract_sha256"]) != contract_hash:
            conflicts.append("CONTRACT_IDENTITY_CONFLICT")

        conflicts = list(dict.fromkeys(conflicts))
        status = "BLOCKED" if conflicts else "IMPORTED"
        stat = source.stat()
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
                digest,
                len(raw),
                stat.st_mtime_ns,
                str(value.get("protocol_version") or "UNKNOWN"),
                status,
                canonical_json(conflicts),
                utc_now(),
            ),
        )
        if status == "IMPORTED" and existing_contract is None:
            now = utc_now()
            conn.execute(
                """
                INSERT INTO contracts(
                    project_id,root_contract_json,root_contract_sha256,
                    acceptance_contract_json,acceptance_contract_sha256,
                    contract_sha256,imported_snapshot_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    project_id,
                    canonical_json(root_value),
                    root_hash,
                    canonical_json(acceptance_value),
                    acceptance_hash,
                    contract_hash,
                    snapshot_id,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO project_state(project_id,updated_at) VALUES(?,?)",
                (project_id, now),
            )
        row = conn.execute(
            "SELECT * FROM imported_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        return _from_row(row)
