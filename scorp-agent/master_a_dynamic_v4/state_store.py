from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from .contracts import ProposalRejected, validate_proposal
from .models import CommitResult, IntentState, canonical_json, sha256_json


UTC = dt.timezone.utc
SCHEMA_VERSION = 5
_UNSET = object()


class StoreInvariantError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StateStore:
    def __init__(self, path: str | pathlib.Path, allowed_roots: Sequence[str | pathlib.Path]):
        self.path = pathlib.Path(path).resolve()
        self.allowed_roots = tuple(pathlib.Path(root).resolve() for root in allowed_roots)
        if not self.allowed_roots:
            raise StoreInvariantError("ALLOWED_ROOTS_EMPTY")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False
        self._migrate()

    def __enter__(self) -> "StateStore":
        if self._closed:
            raise StoreInvariantError("STORE_CLOSED")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    def _assert_open(self) -> None:
        if self._closed:
            raise StoreInvariantError("STORE_CLOSED")

    def _connect(self) -> sqlite3.Connection:
        self._assert_open()
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            journal_mode = str(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            observed = {
                "journal_mode": journal_mode,
                "synchronous": int(conn.execute("PRAGMA synchronous").fetchone()[0]),
                "foreign_keys": int(conn.execute("PRAGMA foreign_keys").fetchone()[0]),
                "busy_timeout": int(conn.execute("PRAGMA busy_timeout").fetchone()[0]),
            }
            expected = {
                "journal_mode": "delete",
                "synchronous": 2,
                "foreign_keys": 1,
                "busy_timeout": 5000,
            }
            if observed != expected:
                raise StoreInvariantError(f"SQLITE_PRAGMA_MISMATCH observed={observed!r}")
            return conn
        except Exception:
            conn.close()
            raise

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _migrate(self) -> None:
        schema_path = pathlib.Path(__file__).with_name("schema.sql")
        schema_bytes = schema_path.read_bytes()
        schema_text = schema_bytes.decode("utf-8")
        schema_hash = hashlib.sha256(schema_bytes).hexdigest()
        with self._connection() as conn:
            conn.executescript("BEGIN IMMEDIATE;\n" + schema_text + "\nCOMMIT;")
            assignment_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(assignments)").fetchall()
            }
            if "base_state_version" not in assignment_columns:
                conn.execute(
                    "ALTER TABLE assignments ADD COLUMN base_state_version INTEGER NOT NULL DEFAULT 0 CHECK (base_state_version >= 0)"
                )
            task_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(task_nodes)").fetchall()
            }
            if "task_context_json" not in task_columns:
                conn.execute(
                    "ALTER TABLE task_nodes ADD COLUMN task_context_json TEXT NOT NULL DEFAULT '{}'"
                )
            observation_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(runtime_observations)").fetchall()
            }
            if "last_heartbeat_at" not in observation_columns:
                conn.execute("ALTER TABLE runtime_observations ADD COLUMN last_heartbeat_at TEXT")
                conn.execute(
                    "UPDATE runtime_observations SET last_heartbeat_at=last_observed_at "
                    "WHERE last_heartbeat_at IS NULL"
                )
            rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            if not rows:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "INSERT INTO schema_migrations(version,applied_at,schema_sha256) VALUES(?,?,?)",
                        (SCHEMA_VERSION, utc_now(), schema_hash),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            versions = [int(row[0]) for row in rows]
            # Version 2 adds the daemon lease table. Version 3 persists the
            # non-authoritative task context used to build a Worker prompt.
            # The DDL above is idempotent, so recording each migration is
            # sufficient for older databases and preserves their history.
            if versions == [1] and SCHEMA_VERSION >= 2:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "INSERT INTO schema_migrations(version,applied_at,schema_sha256) VALUES(?,?,?)",
                        (2, utc_now(), schema_hash),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                versions = [1, 2]
            if versions == [1, 2] and SCHEMA_VERSION >= 3:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "INSERT INTO schema_migrations(version,applied_at,schema_sha256) VALUES(?,?,?)",
                        (3, utc_now(), schema_hash),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                versions = [1, 2, 3]
            if versions in ([1, 2, 3], [3]) and SCHEMA_VERSION >= 4:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "INSERT INTO schema_migrations(version,applied_at,schema_sha256) VALUES(?,?,?)",
                        (4, utc_now(), schema_hash),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                versions = [1, 2, 3, 4] if versions == [1, 2, 3] else [3, 4]
            if versions in ([1, 2, 3, 4], [3, 4], [4]) and SCHEMA_VERSION >= 5:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(
                        "INSERT INTO schema_migrations(version,applied_at,schema_sha256) VALUES(?,?,?)",
                        (5, utc_now(), schema_hash),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                versions = versions + [5]
            valid_versions = {
                tuple(range(1, SCHEMA_VERSION + 1)),
                (SCHEMA_VERSION,),
                (3, 4, 5),
            }
            if tuple(versions) not in valid_versions:
                raise StoreInvariantError(f"SCHEMA_VERSION_UNSUPPORTED actual={versions!r}")

    def connection_settings(self) -> dict[str, Any]:
        with self._connection() as conn:
            return {
                "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "synchronous": int(conn.execute("PRAGMA synchronous").fetchone()[0]),
                "foreign_keys": int(conn.execute("PRAGMA foreign_keys").fetchone()[0]),
                "busy_timeout": int(conn.execute("PRAGMA busy_timeout").fetchone()[0]),
            }

    def table_names(self) -> list[str]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return [str(row[0]) for row in rows]

    @contextlib.contextmanager
    def _transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_contract(
        self,
        project_id: str,
        *,
        root_contract: Mapping[str, Any],
        acceptance_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        if not project or not isinstance(root_contract, Mapping) or not root_contract:
            raise StoreInvariantError("ROOT_CONTRACT_INVALID")
        if not isinstance(acceptance_contract, Mapping) or not acceptance_contract:
            raise StoreInvariantError("ACCEPTANCE_CONTRACT_INVALID")
        root_value = dict(root_contract)
        acceptance_value = dict(acceptance_contract)
        root_hash = sha256_json(root_value)
        acceptance_hash = sha256_json(acceptance_value)
        contract_hash = sha256_json(
            {
                "root_contract_sha256": root_hash,
                "acceptance_contract_sha256": acceptance_hash,
            }
        )
        now = utc_now()
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM contracts WHERE project_id=?", (project,)
            ).fetchone()
            if existing is not None:
                if str(existing["contract_sha256"]) != contract_hash:
                    raise StoreInvariantError("CONTRACT_IDENTITY_MISMATCH")
                # A project created before schema v4 has the contract and
                # project_state rows but no runtime control/observation rows.
                # Repair those rows transactionally when the contract is
                # reopened; never invent an event history for the old state.
                conn.execute(
                    """
                    INSERT OR IGNORE INTO operator_controls(
                        project_id,operator_state,operator_generation,objective_generation,
                        objective_sha256,updated_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (project, "RUNNING", 0, 0, str(root_value.get("objective_sha256") or root_hash), now),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO runtime_observations(
                        project_id,progress_state,browser_semantic_state,auth_host_blocker,
                        last_observed_at,last_heartbeat_at,last_progress_at,last_state_change_at,
                        last_content_change_at,last_browser_success_at,last_browser_error_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (project, "IDLE", "UNKNOWN", None, now, now, None, now, None, None, None),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO daemon_supervision(
                        project_id,restart_count,recovery_count,consecutive_failures,
                        restart_budget,circuit_state,backoff_until,block_reason,
                        last_failure_at,last_recovery_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (project, 0, 0, 0, 3, "CLOSED", None, None, None, None, now),
                )
                return dict(existing)
            conn.execute(
                """
                INSERT INTO contracts(
                    project_id,root_contract_json,root_contract_sha256,
                    acceptance_contract_json,acceptance_contract_sha256,
                    contract_sha256,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    project,
                    canonical_json(root_value),
                    root_hash,
                    canonical_json(acceptance_value),
                    acceptance_hash,
                    contract_hash,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO project_state(project_id,updated_at) VALUES(?,?)",
                (project, now),
            )
            conn.execute(
                """
                INSERT INTO operator_controls(
                    project_id,operator_state,operator_generation,objective_generation,
                    objective_sha256,updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (project, "RUNNING", 0, 0, str(root_value.get("objective_sha256") or sha256_json(root_value)), now),
            )
            conn.execute(
                """
                INSERT INTO runtime_observations(
                    project_id,progress_state,browser_semantic_state,auth_host_blocker,
                    last_observed_at,last_heartbeat_at,last_progress_at,last_state_change_at,
                    last_content_change_at,last_browser_success_at,last_browser_error_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (project, "IDLE", "UNKNOWN", None, now, now, None, now, None, None, None),
            )
            conn.execute(
                """
                INSERT INTO daemon_supervision(
                    project_id,restart_count,recovery_count,consecutive_failures,
                    restart_budget,circuit_state,backoff_until,block_reason,
                    last_failure_at,last_recovery_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (project, 0, 0, 0, 3, "CLOSED", None, None, None, None, now),
            )
            return dict(
                conn.execute("SELECT * FROM contracts WHERE project_id=?", (project,)).fetchone()
            )

    def get_operator_control(self, project_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM operator_controls WHERE project_id=?", (str(project_id),)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("OPERATOR_CONTROL_NOT_FOUND")
            return dict(row)

    def get_runtime_observation(self, project_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_observations WHERE project_id=?", (str(project_id),)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("RUNTIME_OBSERVATION_NOT_FOUND")
            return dict(row)

    def record_runtime_observation(
        self,
        project_id: str,
        *,
        progress_state: str,
        browser_semantic_state: str | None | object = _UNSET,
        auth_host_blocker: str | None | object = _UNSET,
        observed_at: str | None = None,
        content_changed: bool = False,
        progress_made: bool = False,
        browser_succeeded: bool = False,
        browser_error: bool = False,
    ) -> dict[str, Any]:
        """Persist bounded liveness facts; IDLE never advances progress time."""
        project = str(project_id or "").strip()
        if not project or not str(progress_state or "").strip():
            raise StoreInvariantError("RUNTIME_OBSERVATION_INVALID")
        stamp = str(observed_at or utc_now())
        with self._transaction() as conn:
            current = conn.execute(
                "SELECT * FROM runtime_observations WHERE project_id=?", (project,)
            ).fetchone()
            if current is None:
                raise StoreInvariantError("RUNTIME_OBSERVATION_NOT_FOUND")
            semantic = current["browser_semantic_state"] if browser_semantic_state is _UNSET else browser_semantic_state
            blocker = current["auth_host_blocker"] if auth_host_blocker is _UNSET else auth_host_blocker
            changed = (
                str(current["progress_state"]) != str(progress_state)
                or str(current["browser_semantic_state"]) != str(semantic)
                or current["auth_host_blocker"] != blocker
            )
            last_progress = current["last_progress_at"]
            if bool(progress_made) or bool(content_changed):
                last_progress = stamp
            last_content = stamp if content_changed else current["last_content_change_at"]
            last_success = stamp if browser_succeeded else current["last_browser_success_at"]
            last_error = stamp if browser_error else current["last_browser_error_at"]
            conn.execute(
                """
                UPDATE runtime_observations SET progress_state=?,browser_semantic_state=?,
                    auth_host_blocker=?,last_observed_at=?,last_heartbeat_at=?,last_progress_at=?,
                    last_state_change_at=?,last_content_change_at=?,last_browser_success_at=?,
                    last_browser_error_at=? WHERE project_id=?
                """,
                (
                    str(progress_state), semantic, blocker, stamp, stamp, last_progress,
                    stamp if changed else current["last_state_change_at"], last_content,
                    last_success, last_error, project,
                ),
            )
            return dict(conn.execute("SELECT * FROM runtime_observations WHERE project_id=?", (project,)).fetchone())

    def get_runtime_command_receipt(self, request_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_command_receipts WHERE request_id=?", (str(request_id),)
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["response"] = json.loads(str(value.pop("response_json")))
            return value

    # Public names retained by the runtime command plan. Keep the explicit
    # runtime_* methods as the canonical implementation names.
    def get_command_receipt(self, request_id: str) -> dict[str, Any] | None:
        return self.get_runtime_command_receipt(request_id)

    def record_runtime_command_receipt(
        self,
        *,
        request_id: str,
        receipt_id: str,
        project_id: str,
        command: str,
        actor: str,
        input_state_version: int | None,
        output_state_version: int | None,
        daemon_epoch: int | None,
        master_epoch: int | None,
        operator_generation: int | None,
        objective_generation: int | None,
        payload_sha256: str,
        status: str,
        reason: str | None,
        response: Mapping[str, Any],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM runtime_command_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            response_json = canonical_json(dict(response))
            if existing is not None:
                if (
                    str(existing["command"]) != str(command)
                    or str(existing["payload_sha256"]) != str(payload_sha256)
                ):
                    raise StoreInvariantError("RUNTIME_RECEIPT_IDEMPOTENCY_CONFLICT")
                value = dict(existing)
                value["response"] = json.loads(str(value.pop("response_json")))
                return value
            stamp = str(created_at or utc_now())
            conn.execute(
                """
                INSERT INTO runtime_command_receipts(
                    request_id,receipt_id,project_id,command,actor,input_state_version,
                    output_state_version,daemon_epoch,master_epoch,operator_generation,
                    objective_generation,payload_sha256,status,reason,response_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id, receipt_id, project_id, command, actor, input_state_version,
                    output_state_version, daemon_epoch, master_epoch, operator_generation,
                    objective_generation, payload_sha256, status, reason, response_json, stamp,
                ),
            )
            value = dict(conn.execute("SELECT * FROM runtime_command_receipts WHERE request_id=?", (request_id,)).fetchone())
            value["response"] = json.loads(str(value.pop("response_json")))
            return value

    def record_command_receipt(self, **kwargs: Any) -> dict[str, Any]:
        return self.record_runtime_command_receipt(**kwargs)

    def get_contract(self, project_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM contracts WHERE project_id=?", (project_id,)).fetchone()
            if row is None:
                raise StoreInvariantError("CONTRACT_NOT_FOUND")
            return dict(row)

    def get_project_state(self, project_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM project_state WHERE project_id=?", (project_id,)).fetchone()
            if row is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            return dict(row)

    def get_daemon_supervision(self, project_id: str) -> dict[str, Any]:
        project = str(project_id or "").strip()
        if not project:
            raise StoreInvariantError("PROJECT_ID_EMPTY")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM daemon_supervision WHERE project_id=?", (project,)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("DAEMON_SUPERVISION_NOT_FOUND")
            return dict(row)

    def daemon_recovery_gate(
        self, project_id: str, *, now: dt.datetime | None = None
    ) -> dict[str, Any]:
        row = self.get_daemon_supervision(project_id)
        instant = self._aware_time(now)
        stamp = instant.isoformat().replace("+00:00", "Z")
        state = str(row["circuit_state"])
        if state == "BLOCKED":
            return {
                "status": "BLOCKED",
                "reason": str(row["block_reason"] or "RESTART_BUDGET_EXHAUSTED"),
                "project_id": str(project_id),
                "restart_count": int(row["restart_count"]),
                "restart_budget": int(row["restart_budget"]),
            }
        backoff_until = str(row["backoff_until"] or "")
        if state == "BACKOFF" and backoff_until and backoff_until > stamp:
            return {
                "status": "BACKOFF",
                "reason": "DAEMON_RESTART_BACKOFF",
                "retry_at": backoff_until,
                "project_id": str(project_id),
                "restart_count": int(row["restart_count"]),
                "restart_budget": int(row["restart_budget"]),
            }
        return {
            "status": "ALLOWED",
            "project_id": str(project_id),
            "restart_count": int(row["restart_count"]),
            "restart_budget": int(row["restart_budget"]),
        }

    def _record_daemon_failure_in_transaction(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        *,
        reason: str,
        stamp: str,
        restart_budget: int | None = None,
        base_backoff_seconds: int = 2,
        max_backoff_seconds: int = 300,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM daemon_supervision WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None:
            raise StoreInvariantError("DAEMON_SUPERVISION_NOT_FOUND")
        budget = int(restart_budget if restart_budget is not None else row["restart_budget"])
        if budget < 1:
            raise StoreInvariantError("DAEMON_RESTART_BUDGET_INVALID")
        base = max(1, int(base_backoff_seconds))
        maximum = max(base, int(max_backoff_seconds))
        restart_count = int(row["restart_count"]) + 1
        consecutive = int(row["consecutive_failures"]) + 1
        delay = min(maximum, base * (2 ** min(consecutive - 1, 10)))
        backoff = (
            dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            + dt.timedelta(seconds=delay)
        ).isoformat().replace("+00:00", "Z")
        blocked = restart_count >= budget
        circuit = "BLOCKED" if blocked else "BACKOFF"
        block_reason = "RESTART_BUDGET_EXHAUSTED" if blocked else None
        conn.execute(
            """
            UPDATE daemon_supervision SET restart_count=?,consecutive_failures=?,
                restart_budget=?,circuit_state=?,backoff_until=?,block_reason=?,
                last_failure_at=?,updated_at=? WHERE project_id=?
            """,
            (
                restart_count,
                consecutive,
                budget,
                circuit,
                backoff,
                block_reason,
                stamp,
                stamp,
                project_id,
            ),
        )
        conn.execute(
            "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
            (
                f"daemon-failure-{project_id}-{uuid.uuid4().hex}",
                project_id,
                "DAEMON_FAILURE",
                canonical_json({
                    "reason": str(reason),
                    "restart_count": restart_count,
                    "consecutive_failures": consecutive,
                    "restart_budget": budget,
                    "circuit_state": circuit,
                    "backoff_until": backoff,
                }),
                stamp,
            ),
        )
        return dict(
            conn.execute(
                "SELECT * FROM daemon_supervision WHERE project_id=?", (project_id,)
            ).fetchone()
        )

    def record_daemon_failure(
        self,
        project_id: str,
        *,
        reason: str,
        now: dt.datetime | None = None,
        restart_budget: int | None = None,
        base_backoff_seconds: int = 2,
        max_backoff_seconds: int = 300,
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        why = str(reason or "").strip()
        if not project or not why:
            raise StoreInvariantError("DAEMON_FAILURE_INVALID")
        stamp = self._aware_time(now).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM project_state WHERE project_id=?", (project,)
            ).fetchone() is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            return self._record_daemon_failure_in_transaction(
                conn,
                project,
                reason=why,
                stamp=stamp,
                restart_budget=restart_budget,
                base_backoff_seconds=base_backoff_seconds,
                max_backoff_seconds=max_backoff_seconds,
            )

    def record_daemon_recovery(
        self, project_id: str, *, now: dt.datetime | None = None
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        if not project:
            raise StoreInvariantError("PROJECT_ID_EMPTY")
        stamp = self._aware_time(now).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM daemon_supervision WHERE project_id=?", (project,)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("DAEMON_SUPERVISION_NOT_FOUND")
            conn.execute(
                """
                UPDATE daemon_supervision SET restart_count=0,recovery_count=recovery_count+1,
                    consecutive_failures=0,circuit_state='CLOSED',backoff_until=NULL,
                    block_reason=NULL,last_recovery_at=?,updated_at=? WHERE project_id=?
                """,
                (stamp, stamp, project),
            )
            conn.execute(
                "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    f"daemon-recovery-{project}-{uuid.uuid4().hex}",
                    project,
                    "DAEMON_RECOVERY",
                    canonical_json({"recovery_count": int(row["recovery_count"]) + 1}),
                    stamp,
                ),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM daemon_supervision WHERE project_id=?", (project,)
                ).fetchone()
            )

    def acquire_daemon_lease(
        self,
        project_id: str,
        owner_id: str,
        *,
        now: dt.datetime | None = None,
        ttl_seconds: int = 30,
    ) -> dict[str, Any]:
        """Acquire the single local-daemon lease for a project.

        A live lease owned by another process is a hard fencing error. An
        expired lease advances the epoch before another owner can proceed.
        The operation is one SQLite transaction, so two daemon processes
        cannot both become the current owner.
        """
        project = str(project_id or "").strip()
        owner = str(owner_id or "").strip()
        ttl = int(ttl_seconds)
        if not project or not owner or ttl <= 0:
            raise StoreInvariantError("DAEMON_LEASE_INVALID")
        instant = self._aware_time(now)
        stamp = instant.isoformat().replace("+00:00", "Z")
        lease_until = (instant + dt.timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM project_state WHERE project_id=?", (project,)).fetchone() is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            existing = conn.execute("SELECT * FROM daemon_leases WHERE project_id=?", (project,)).fetchone()
            if existing is not None and str(existing["lease_until"]) > stamp:
                if str(existing["owner_id"]) != owner:
                    raise StoreInvariantError("DAEMON_LEASE_ACTIVE")
                conn.execute(
                    "UPDATE daemon_leases SET heartbeat_at=?,lease_until=? WHERE project_id=? AND owner_id=? AND daemon_epoch=?",
                    (stamp, lease_until, project, owner, int(existing["daemon_epoch"])),
                )
                return dict(conn.execute("SELECT * FROM daemon_leases WHERE project_id=?", (project,)).fetchone())
            if existing is not None:
                # An expired lease is the durable evidence available to the
                # next process after an unclean daemon stop.  Record the
                # restart before installing the new epoch; the new owner is
                # still the only authority for this transaction.
                self._record_daemon_failure_in_transaction(
                    conn,
                    project,
                    reason="LEASE_EXPIRED",
                    stamp=stamp,
                )
            epoch = int(existing["daemon_epoch"]) + 1 if existing is not None else 1
            conn.execute(
                """
                INSERT INTO daemon_leases(project_id,daemon_epoch,owner_id,heartbeat_at,lease_until)
                VALUES(?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET
                    daemon_epoch=excluded.daemon_epoch,
                    owner_id=excluded.owner_id,
                    heartbeat_at=excluded.heartbeat_at,
                    lease_until=excluded.lease_until
                """,
                (project, epoch, owner, stamp, lease_until),
            )
            conn.execute(
                "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    f"daemon-lease-{project}-{epoch}",
                    project,
                    "DAEMON_LEASE_ACQUIRED",
                    canonical_json({"daemon_epoch": epoch, "owner_id": owner}),
                    stamp,
                ),
            )
            return dict(conn.execute("SELECT * FROM daemon_leases WHERE project_id=?", (project,)).fetchone())

    def heartbeat_daemon_lease(
        self,
        project_id: str,
        owner_id: str,
        *,
        daemon_epoch: int,
        now: dt.datetime | None = None,
        ttl_seconds: int = 30,
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        owner = str(owner_id or "").strip()
        ttl = int(ttl_seconds)
        if not project or not owner or int(daemon_epoch) < 1 or ttl <= 0:
            raise StoreInvariantError("DAEMON_HEARTBEAT_INVALID")
        instant = self._aware_time(now)
        stamp = instant.isoformat().replace("+00:00", "Z")
        lease_until = (instant + dt.timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM daemon_leases WHERE project_id=?", (project,)).fetchone()
            if row is None:
                raise StoreInvariantError("DAEMON_LEASE_NOT_FOUND")
            if str(row["owner_id"]) != owner or int(row["daemon_epoch"]) != int(daemon_epoch):
                raise StoreInvariantError("DAEMON_LEASE_FENCED")
            if str(row["lease_until"]) <= stamp:
                raise StoreInvariantError("DAEMON_LEASE_EXPIRED")
            conn.execute(
                "UPDATE daemon_leases SET heartbeat_at=?,lease_until=? WHERE project_id=? AND owner_id=? AND daemon_epoch=?",
                (stamp, lease_until, project, owner, int(daemon_epoch)),
            )
            return dict(conn.execute("SELECT * FROM daemon_leases WHERE project_id=?", (project,)).fetchone())

    def activation_snapshot(self, project_id: str, *, daemon_epoch: int):
        """Read the bounded state required by the local ActivationArbiter.

        This method is intentionally read-only.  It never claims a lease,
        touches a browser intent, or advances an epoch.
        """
        from .activation_arbiter import ArbiterSnapshot

        project = str(project_id or "").strip()
        if not project:
            raise StoreInvariantError("PROJECT_ID_EMPTY")
        now = utc_now()
        with self._connection() as conn:
            state = conn.execute(
                "SELECT status,master_epoch FROM project_state WHERE project_id=?", (project,)
            ).fetchone()
            if state is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            active_master = conn.execute(
                """
                SELECT 1 FROM master_sessions
                WHERE project_id=? AND state='ACTIVE' AND master_epoch=? AND lease_until>?
                LIMIT 1
                """,
                (project, int(state["master_epoch"]), now),
            ).fetchone() is not None
            active_workers = int(conn.execute(
                "SELECT COUNT(*) FROM leases WHERE project_id=? AND state='ACTIVE' AND expires_at>?",
                (project, now),
            ).fetchone()[0])
            ready_tasks = int(conn.execute(
                """
                SELECT COUNT(*) FROM task_nodes t
                WHERE t.project_id=? AND t.state='QUEUED'
                  AND NOT EXISTS (
                    SELECT 1 FROM task_dependencies d
                    JOIN task_nodes dep ON dep.project_id=d.project_id AND dep.task_id=d.depends_on_task_id
                    WHERE d.project_id=t.project_id AND d.task_id=t.task_id
                      AND dep.state NOT IN ('VERIFIED','ACCEPTED')
                  )
                """,
                (project,),
            ).fetchone()[0])
            ambiguous = int(conn.execute(
                "SELECT COUNT(*) FROM action_intents WHERE project_id=? AND state IN ('MAY_HAVE_SUBMITTED','BLOCKED_AMBIGUOUS')",
                (project,),
            ).fetchone()[0])
            stale = int(conn.execute(
                """
                SELECT COUNT(*) FROM candidate_results r
                JOIN project_state p ON p.project_id=r.project_id
                WHERE r.project_id=? AND r.master_epoch<p.master_epoch
                """,
                (project,),
            ).fetchone()[0])
            control = conn.execute(
                "SELECT operator_state FROM operator_controls WHERE project_id=?", (project,)
            ).fetchone()
            observation = conn.execute(
                "SELECT progress_state,auth_host_blocker,browser_semantic_state FROM runtime_observations WHERE project_id=?",
                (project,),
            ).fetchone()
            pending_results = int(conn.execute(
                "SELECT COUNT(*) FROM candidate_results WHERE project_id=? AND verification_state='PENDING'",
                (project,),
            ).fetchone()[0])
            free_slots = max(0, 2 - active_workers)
            progress_state = str(observation["progress_state"]) if observation is not None else ("ACTIVE_NO_VISIBLE_PROGRESS" if active_workers else "IDLE")
            return ArbiterSnapshot(
                project_id=project,
                project_status=str(state["status"]),
                master_epoch=int(state["master_epoch"]),
                daemon_epoch=int(daemon_epoch),
                master_active=active_master,
                active_workers=active_workers,
                free_slots=free_slots,
                ready_tasks=ready_tasks,
                ambiguous_intents=ambiguous,
                progress_state=progress_state,
                stale_results=stale,
                # Missing operator authority is not equivalent to RUNNING;
                # surface it as an explicit unknown fence so the Arbiter
                # blocks before any new work can be admitted.
                operator_state=str(control["operator_state"]) if control is not None else "UNKNOWN",
                auth_host_blocker=str(observation["auth_host_blocker"]) if observation is not None and observation["auth_host_blocker"] else None,
                pending_results=pending_results,
                browser_semantic_state=str(observation["browser_semantic_state"]) if observation is not None else "UNKNOWN",
            )

    def advance_master_epoch(self, project_id: str, *, expected_epoch: int) -> int:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT master_epoch FROM project_state WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            actual = int(row[0])
            if actual != int(expected_epoch):
                raise StoreInvariantError(
                    f"MASTER_EPOCH_CONFLICT expected={expected_epoch} actual={actual}"
                )
            next_epoch = actual + 1
            conn.execute(
                "UPDATE project_state SET master_epoch=?,updated_at=? WHERE project_id=?",
                (next_epoch, utc_now(), project_id),
            )
            conn.execute(
                "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    f"event-{uuid.uuid4().hex}",
                    project_id,
                    "MASTER_EPOCH_ADVANCED",
                    canonical_json({"previous": actual, "current": next_epoch}),
                    utc_now(),
                ),
            )
            return next_epoch

    @staticmethod
    def _aware_time(value: dt.datetime | None) -> dt.datetime:
        current = value or dt.datetime.now(UTC)
        if current.tzinfo is None:
            raise StoreInvariantError("MASTER_SESSION_TIME_NAIVE")
        return current.astimezone(UTC)

    def start_master_session(
        self,
        project_id: str,
        session_id: str,
        *,
        now: dt.datetime | None = None,
        ttl_seconds: int = 1500,
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        session = str(session_id or "").strip()
        if not project or not session:
            raise StoreInvariantError("MASTER_SESSION_ID_INVALID")
        ttl = int(ttl_seconds)
        if ttl <= 0:
            raise StoreInvariantError("MASTER_SESSION_TTL_INVALID")
        instant = self._aware_time(now)
        instant_text = instant.isoformat().replace("+00:00", "Z")
        lease_until = (instant + dt.timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            state = conn.execute(
                "SELECT * FROM project_state WHERE project_id=?", (project,)
            ).fetchone()
            if state is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            if str(state["status"]) in {"COMPLETE", "HARD_BLOCKED"}:
                raise StoreInvariantError("MASTER_SESSION_TERMINAL_PROJECT")
            existing = conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND session_id=?",
                (project, session),
            ).fetchone()
            if existing is not None and str(existing["state"]) == "ACTIVE":
                if int(existing["master_epoch"]) == int(state["master_epoch"]) and str(existing["lease_until"]) > instant_text:
                    return dict(existing)
                reason = "EPOCH_FENCED" if int(existing["master_epoch"]) != int(state["master_epoch"]) else "LEASE_EXPIRED"
                conn.execute(
                    "UPDATE master_sessions SET state='STALE',ended_at=?,end_reason=? WHERE project_id=? AND session_id=?",
                    (instant_text, reason, project, session),
                )
            elif existing is not None:
                raise StoreInvariantError("MASTER_SESSION_ID_REUSED")
            active = conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND state='ACTIVE'",
                (project,),
            ).fetchone()
            if active is not None:
                if int(active["master_epoch"]) == int(state["master_epoch"]) and str(active["lease_until"]) > instant_text:
                    raise StoreInvariantError("MASTER_SESSION_ACTIVE")
                reason = "EPOCH_FENCED" if int(active["master_epoch"]) != int(state["master_epoch"]) else "LEASE_EXPIRED"
                conn.execute(
                    "UPDATE master_sessions SET state='STALE',ended_at=?,end_reason=? WHERE project_id=? AND session_id=?",
                    (instant_text, reason, project, active["session_id"]),
                )
            history = conn.execute(
                "SELECT 1 FROM master_sessions WHERE project_id=? LIMIT 1", (project,)
            ).fetchone()
            current_epoch = int(state["master_epoch"])
            if history is not None:
                current_epoch += 1
                conn.execute(
                    "UPDATE project_state SET master_epoch=?,updated_at=? WHERE project_id=?",
                    (current_epoch, instant_text, project),
                )
            conn.execute(
                "INSERT INTO master_sessions(project_id,session_id,master_epoch,state,started_at,heartbeat_at,lease_until) VALUES(?,?,?,?,?,?,?)",
                (project, session, current_epoch, "ACTIVE", instant_text, instant_text, lease_until),
            )
            conn.execute(
                "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    f"master-session-start-{project}-{session}",
                    project,
                    "MASTER_SESSION_STARTED",
                    canonical_json({"session_id": session, "master_epoch": current_epoch}),
                    instant_text,
                ),
            )
            return dict(conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND session_id=?",
                (project, session),
            ).fetchone())

    def heartbeat_master_session(
        self,
        project_id: str,
        session_id: str,
        *,
        master_epoch: int,
        now: dt.datetime | None = None,
        ttl_seconds: int = 1500,
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        session = str(session_id or "").strip()
        ttl = int(ttl_seconds)
        if not project or not session or ttl <= 0:
            raise StoreInvariantError("MASTER_SESSION_HEARTBEAT_INVALID")
        instant = self._aware_time(now)
        instant_text = instant.isoformat().replace("+00:00", "Z")
        lease_until = (instant + dt.timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            state = conn.execute(
                "SELECT master_epoch,status FROM project_state WHERE project_id=?", (project,)
            ).fetchone()
            row = conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND session_id=?", (project, session)
            ).fetchone()
            if state is None or row is None:
                raise StoreInvariantError("MASTER_SESSION_NOT_ACTIVE")
            if int(state["master_epoch"]) != int(master_epoch) or int(row["master_epoch"]) != int(master_epoch):
                raise StoreInvariantError("MASTER_SESSION_FENCED")
            if str(row["state"]) != "ACTIVE":
                raise StoreInvariantError("MASTER_SESSION_NOT_ACTIVE")
            if str(row["lease_until"]) <= instant_text:
                conn.execute(
                    "UPDATE master_sessions SET state='STALE',ended_at=?,end_reason=? WHERE project_id=? AND session_id=?",
                    (instant_text, "LEASE_EXPIRED", project, session),
                )
                raise StoreInvariantError("MASTER_SESSION_EXPIRED")
            conn.execute(
                "UPDATE master_sessions SET heartbeat_at=?,lease_until=? WHERE project_id=? AND session_id=?",
                (instant_text, lease_until, project, session),
            )
            return dict(conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND session_id=?",
                (project, session),
            ).fetchone())

    def inspect_master_session(
        self, project_id: str, *, now: dt.datetime | None = None
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        if not project:
            raise StoreInvariantError("PROJECT_ID_EMPTY")
        instant = self._aware_time(now)
        instant_text = instant.isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            state = conn.execute(
                "SELECT * FROM project_state WHERE project_id=?", (project,)
            ).fetchone()
            if state is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            if str(state["status"]) in {"COMPLETE", "HARD_BLOCKED"}:
                return {"status": "TERMINAL", "project_id": project, "project_state": str(state["status"])}
            row = conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND state='ACTIVE'",
                (project,),
            ).fetchone()
            if row is not None:
                if int(row["master_epoch"]) == int(state["master_epoch"]) and str(row["lease_until"]) > instant_text:
                    return {"status": "MASTER_ACTIVE", "project_id": project, "session_id": str(row["session_id"]), "master_epoch": int(row["master_epoch"]), "lease_until": str(row["lease_until"])}
                reason = "EPOCH_FENCED" if int(row["master_epoch"]) != int(state["master_epoch"]) else "LEASE_EXPIRED"
                conn.execute(
                    "UPDATE master_sessions SET state='STALE',ended_at=?,end_reason=? WHERE project_id=? AND session_id=?",
                    (instant_text, reason, project, row["session_id"]),
                )
                stale_session = str(row["session_id"])
            else:
                stale_session = None
            payload = {"master_epoch": int(state["master_epoch"]), "stale_session_id": stale_session}
            payload_text = canonical_json(payload)
            existing_event = conn.execute(
                "SELECT event_id FROM events WHERE project_id=? AND kind='MASTER_RESUME_REQUIRED' AND payload_json=?",
                (project, payload_text),
            ).fetchone()
            event_id = str(existing_event[0]) if existing_event else f"master-resume-{hashlib.sha256((project + payload_text).encode('utf-8')).hexdigest()[:24]}"
            if existing_event is None:
                conn.execute(
                    "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (event_id, project, "MASTER_RESUME_REQUIRED", payload_text, instant_text),
                )
            result = {"status": "RESUME_REQUIRED", "project_id": project, "master_epoch": int(state["master_epoch"]), "resume_event_id": event_id}
            if stale_session:
                result["stale_session_id"] = stale_session
            return result

    def end_master_session(
        self,
        project_id: str,
        session_id: str,
        *,
        master_epoch: int,
        reason: str,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        session = str(session_id or "").strip()
        why = str(reason or "").strip()
        if not project or not session or not why:
            raise StoreInvariantError("MASTER_SESSION_END_INVALID")
        instant = self._aware_time(now)
        instant_text = instant.isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            state = conn.execute(
                "SELECT master_epoch FROM project_state WHERE project_id=?", (project,)
            ).fetchone()
            row = conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND session_id=?", (project, session)
            ).fetchone()
            if state is None or row is None:
                raise StoreInvariantError("MASTER_SESSION_NOT_FOUND")
            if int(state[0]) != int(master_epoch) or int(row["master_epoch"]) != int(master_epoch):
                raise StoreInvariantError("MASTER_SESSION_FENCED")
            if str(row["state"]) != "ACTIVE":
                return dict(row)
            conn.execute(
                "UPDATE master_sessions SET state='ENDED',ended_at=?,end_reason=?,lease_until=? WHERE project_id=? AND session_id=?",
                (instant_text, why, instant_text, project, session),
            )
            conn.execute(
                "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    f"master-session-end-{project}-{session}",
                    project,
                    "MASTER_SESSION_ENDED",
                    canonical_json({"session_id": session, "master_epoch": int(master_epoch), "reason": why}),
                    instant_text,
                ),
            )
            return dict(conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? AND session_id=?",
                (project, session),
            ).fetchone())

    def _evidence_belongs_to_project(
        self, conn: sqlite3.Connection, project_id: str, refs: Sequence[str]
    ) -> bool:
        for evidence_ref in refs:
            row = conn.execute(
                "SELECT project_id FROM evidence_receipts WHERE evidence_ref=?", (evidence_ref,)
            ).fetchone()
            if row is None or str(row[0]) != project_id:
                return False
        return True

    def commit(
        self,
        expected_version: int,
        master_epoch: int,
        transition_id: str,
        proposal: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> CommitResult:
        transition = str(transition_id or "").strip()
        if not transition or isinstance(evidence_refs, (str, bytes)):
            return CommitResult.REJECTED
        try:
            normalized = validate_proposal(proposal)
            refs = [str(value).strip() for value in evidence_refs]
            if any(not value for value in refs) or len(refs) != len(set(refs)):
                return CommitResult.REJECTED
            expected = int(expected_version)
            epoch = int(master_epoch)
            if expected < 0 or epoch < 0:
                return CommitResult.REJECTED
        except (ProposalRejected, TypeError, ValueError):
            return CommitResult.REJECTED

        project_id = normalized["project_id"]
        proposal_hash = sha256_json(normalized)
        evidence_hash = sha256_json(refs)
        content_hash = sha256_json(
            {
                "project_id": project_id,
                "expected_version": expected,
                "master_epoch": epoch,
                "proposal_sha256": proposal_hash,
                "evidence_refs_sha256": evidence_hash,
            }
        )

        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT content_sha256,result FROM transitions WHERE transition_id=?",
                (transition,),
            ).fetchone()
            if existing is not None:
                if str(existing["content_sha256"]) == content_hash:
                    return CommitResult.ALREADY_COMMITTED
                return CommitResult.REJECTED

            state = conn.execute(
                "SELECT * FROM project_state WHERE project_id=?", (project_id,)
            ).fetchone()
            if state is None:
                return CommitResult.REJECTED
            if int(state["master_epoch"]) != epoch:
                return CommitResult.FENCED
            if int(state["state_version"]) != expected:
                return CommitResult.VERSION_CONFLICT
            if not self._evidence_belongs_to_project(conn, project_id, refs):
                return CommitResult.REJECTED

            next_version = expected + 1
            phase = str(state["phase"])
            if normalized["kind"] == "SET_PHASE":
                phase = normalized["phase"]
            changed = conn.execute(
                """
                UPDATE project_state
                SET state_version=?,phase=?,updated_at=?
                WHERE project_id=? AND state_version=? AND master_epoch=?
                """,
                (next_version, phase, utc_now(), project_id, expected, epoch),
            ).rowcount
            if changed != 1:
                return CommitResult.VERSION_CONFLICT
            conn.execute(
                """
                INSERT INTO transitions(
                    transition_id,project_id,expected_version,master_epoch,
                    proposal_sha256,evidence_refs_sha256,content_sha256,result,
                    committed_version,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    transition,
                    project_id,
                    expected,
                    epoch,
                    proposal_hash,
                    evidence_hash,
                    content_hash,
                    CommitResult.COMMITTED.value,
                    next_version,
                    utc_now(),
                ),
            )
            conn.execute(
                "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (
                    f"event-{uuid.uuid4().hex}",
                    project_id,
                    "STATE_TRANSITION_COMMITTED",
                    canonical_json(
                        {
                            "transition_id": transition,
                            "kind": normalized["kind"],
                            "committed_version": next_version,
                        }
                    ),
                    utc_now(),
                ),
            )
            return CommitResult.COMMITTED

    def count_committed_transitions(self, project_id: str) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM transitions WHERE project_id=? AND result=?",
                (project_id, CommitResult.COMMITTED.value),
            ).fetchone()
            return int(row[0])

    def record_activation_decision(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one deterministic daemon decision using the event log.

        Decisions use the existing transactional event table so the first P0
        implementation does not introduce a second source of truth or require
        a live schema rewrite.  A decision id is idempotent only when its
        canonical payload is identical; reuse with different content is an
        invariant violation.
        """
        if not isinstance(decision, Mapping):
            raise StoreInvariantError("ACTIVATION_DECISION_INVALID")
        required = ("decision_id", "project_id", "actor_id", "daemon_epoch", "master_epoch", "action", "reason", "input_sha256")
        if any(not str(decision.get(key, "")).strip() for key in required):
            raise StoreInvariantError("ACTIVATION_DECISION_FIELDS_INVALID")
        project_id = str(decision["project_id"])
        decision_id = str(decision["decision_id"])
        payload = dict(decision)
        payload_text = canonical_json(payload)
        now = utc_now()
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM project_state WHERE project_id=?", (project_id,)).fetchone() is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            existing = conn.execute(
                "SELECT payload_json FROM events WHERE event_id=? AND project_id=? AND kind='ACTIVATION_DECISION'",
                (decision_id, project_id),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != payload_text:
                    raise StoreInvariantError("ACTIVATION_DECISION_IDENTITY_CONFLICT")
                return payload
            conn.execute(
                "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                (decision_id, project_id, "ACTIVATION_DECISION", payload_text, now),
            )
            return payload

    def count_activation_decisions(self, project_id: str) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM events WHERE project_id=? AND kind='ACTIVATION_DECISION'",
                (str(project_id),),
            ).fetchone()
            return int(row[0])

    def prepare_intent(
        self,
        project_id: str,
        intent_id: str,
        *,
        actor_id: str,
        channel: str,
        action_kind: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        intent = str(intent_id or "").strip()
        actor = str(actor_id or "").strip()
        target_channel = str(channel or "").strip()
        action = str(action_kind or "").strip()
        if not all((intent, actor, target_channel, action)) or not isinstance(payload, Mapping):
            raise StoreInvariantError("INTENT_INVALID")
        payload_value = dict(payload)
        payload_text = canonical_json(payload_value)
        payload_hash = sha256_json(payload_value)
        now = utc_now()
        with self._transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM project_state WHERE project_id=?", (project_id,)
            ).fetchone() is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            existing = conn.execute(
                "SELECT * FROM action_intents WHERE intent_id=?", (intent,)
            ).fetchone()
            if existing is not None:
                identity = (
                    existing["project_id"],
                    existing["actor_id"],
                    existing["channel"],
                    existing["action_kind"],
                    existing["payload_sha256"],
                )
                expected = (project_id, actor, target_channel, action, payload_hash)
                if identity != expected:
                    raise StoreInvariantError("INTENT_IDENTITY_CONFLICT")
                return dict(existing)
            conn.execute(
                """
                INSERT INTO action_intents(
                    intent_id,project_id,actor_id,channel,action_kind,payload_json,
                    payload_sha256,state,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'PREPARED',?,?)
                """,
                (
                    intent,
                    project_id,
                    actor,
                    target_channel,
                    action,
                    payload_text,
                    payload_hash,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO outbox(outbox_id,project_id,intent_id,kind,state,available_at) VALUES(?,?,?,?,?,?)",
                (f"outbox-{intent}", project_id, intent, action, "PENDING", now),
            )
            return dict(
                conn.execute("SELECT * FROM action_intents WHERE intent_id=?", (intent,)).fetchone()
            )

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("INTENT_NOT_FOUND")
            return dict(row)

    def get_outbox_for_intent(self, intent_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE intent_id=?", (intent_id,)).fetchone()
            if row is None:
                raise StoreInvariantError("OUTBOX_NOT_FOUND")
            return dict(row)

    def external_side_effect_gate(
        self,
        project_id: str,
        operator_generation: int,
        objective_generation: int,
    ) -> dict[str, Any]:
        """Validate a generation token immediately before external I/O.

        The returned record is evidence for the caller; the method performs no
        browser, process, filesystem, or network operation. A paused,
        cancelled, superseded, or stale token raises a fail-closed invariant.
        """
        project = str(project_id or "").strip()
        try:
            operator = int(operator_generation)
            objective = int(objective_generation)
        except (TypeError, ValueError) as exc:
            raise StoreInvariantError("OPERATOR_GENERATION_INVALID") from exc
        if operator < 0 or objective < 0:
            raise StoreInvariantError("OPERATOR_GENERATION_INVALID")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT operator_state,operator_generation,objective_generation,objective_sha256,updated_at FROM operator_controls WHERE project_id=?",
                (project,),
            ).fetchone()
            if row is None:
                raise StoreInvariantError("OPERATOR_CONTROL_NOT_FOUND")
            if str(row["operator_state"]) not in {"ACTIVE", "RUNNING"}:
                raise StoreInvariantError(f"OPERATOR_STATE_FENCED:{row['operator_state']}")
            if operator != int(row["operator_generation"]):
                raise StoreInvariantError("OPERATOR_GENERATION_FENCED")
            if objective != int(row["objective_generation"]):
                raise StoreInvariantError("OBJECTIVE_GENERATION_FENCED")
            return {
                "status": "ALLOWED",
                "project_id": project,
                "operator_state": str(row["operator_state"]),
                "operator_generation": int(row["operator_generation"]),
                "objective_generation": int(row["objective_generation"]),
                "objective_sha256": str(row["objective_sha256"]),
                "updated_at": str(row["updated_at"]),
            }

    def runtime_snapshot(self, project_id: str, daemon_epoch: int) -> dict[str, Any]:
        """Return one bounded SQLite-only snapshot for local status callers."""
        project = str(project_id or "").strip()
        if not project:
            raise StoreInvariantError("PROJECT_ID_EMPTY")
        with self._connection() as conn:
            state = conn.execute(
                "SELECT project_id,state_version,master_epoch,status,phase,completion_candidate_commit,updated_at FROM project_state WHERE project_id=?",
                (project,),
            ).fetchone()
            if state is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            contract = conn.execute(
                "SELECT contract_sha256 FROM contracts WHERE project_id=?", (project,)
            ).fetchone()
            control = conn.execute(
                "SELECT * FROM operator_controls WHERE project_id=?", (project,)
            ).fetchone()
            observation = conn.execute(
                "SELECT * FROM runtime_observations WHERE project_id=?", (project,)
            ).fetchone()
            daemon = conn.execute(
                "SELECT daemon_epoch,owner_id,heartbeat_at,lease_until FROM daemon_leases WHERE project_id=?",
                (project,),
            ).fetchone()
            supervision = conn.execute(
                "SELECT * FROM daemon_supervision WHERE project_id=?", (project,)
            ).fetchone()
            master = conn.execute(
                "SELECT * FROM master_sessions WHERE project_id=? ORDER BY CASE WHEN state='ACTIVE' THEN 0 ELSE 1 END, heartbeat_at DESC LIMIT 1",
                (project,),
            ).fetchone()
            active_workers = int(conn.execute(
                "SELECT COUNT(*) FROM leases WHERE project_id=? AND state='ACTIVE'",
                (project,),
            ).fetchone()[0])
            queued = int(conn.execute(
                "SELECT COUNT(*) FROM task_nodes WHERE project_id=? AND state='QUEUED'",
                (project,),
            ).fetchone()[0])
            running = int(conn.execute(
                "SELECT COUNT(*) FROM task_nodes WHERE project_id=? AND state IN ('RUNNING','ASSIGNED')",
                (project,),
            ).fetchone()[0])
            ambiguous = int(conn.execute(
                "SELECT COUNT(*) FROM action_intents WHERE project_id=? AND state IN ('MAY_HAVE_SUBMITTED','BLOCKED_AMBIGUOUS')",
                (project,),
            ).fetchone()[0])
            pending_results = int(conn.execute(
                "SELECT COUNT(*) FROM candidate_results WHERE project_id=? AND verification_state='PENDING'",
                (project,),
            ).fetchone()[0])
            decision = conn.execute(
                "SELECT payload_json FROM events WHERE project_id=? AND kind='ACTIVATION_DECISION' ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
        return {
            "project_id": project,
            "daemon_epoch": int(daemon_epoch),
            "contract_sha256": str(contract[0]) if contract is not None else None,
            "project": dict(state),
            "operator": dict(control) if control is not None else None,
            "observation": dict(observation) if observation is not None else None,
            "daemon": dict(daemon) if daemon is not None else None,
            "supervision": dict(supervision) if supervision is not None else None,
            "master": dict(master) if master is not None else None,
            "workers": {"active": active_workers, "capacity": 2, "free": max(0, 2 - active_workers)},
            "tasks": {"queued": queued, "running": running},
            "reconciliation": {"ambiguous_intents": ambiguous, "pending_results": pending_results},
            "pending_results": pending_results,
            "last_decision": json.loads(str(decision[0])) if decision is not None else None,
        }

    @staticmethod
    def _check_intent_generation_row(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
        """Fence new side effects whose durable operator generation is stale.

        Old imported intents may not carry generation fields and remain
        reconcilable for migration compatibility. Every new browser/local
        intent created by the V4 adapters carries both fields.
        """
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, Mapping):
            raise StoreInvariantError("INTENT_PAYLOAD_INVALID")
        if "operator_generation" not in payload and "objective_generation" not in payload:
            return
        if "operator_generation" not in payload or "objective_generation" not in payload:
            raise StoreInvariantError("OPERATOR_GENERATION_BINDING_INVALID")
        control = conn.execute(
            "SELECT operator_state,operator_generation,objective_generation FROM operator_controls WHERE project_id=?",
            (str(row["project_id"]),),
        ).fetchone()
        if control is None:
            raise StoreInvariantError("OPERATOR_CONTROL_NOT_FOUND")
        try:
            operator_generation = int(payload["operator_generation"])
            objective_generation = int(payload["objective_generation"])
        except (TypeError, ValueError) as exc:
            raise StoreInvariantError("OPERATOR_GENERATION_BINDING_INVALID") from exc
        if (
            str(control["operator_state"]) not in {"ACTIVE", "RUNNING"}
            or operator_generation != int(control["operator_generation"])
            or objective_generation != int(control["objective_generation"])
        ):
            raise StoreInvariantError("OPERATOR_GENERATION_FENCED")

    def assert_intent_generation(self, intent_id: str) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("INTENT_NOT_FOUND")
            self._check_intent_generation_row(conn, row)

    def begin_possible_submit(self, intent_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise StoreInvariantError("INTENT_NOT_FOUND")
            if row["state"] not in {
                IntentState.PREPARED.value,
                IntentState.VERIFIED_NOT_SUBMITTED.value,
            }:
                raise StoreInvariantError(f"INTENT_NOT_SUBMITTABLE state={row['state']}")
            self._check_intent_generation_row(conn, row)
            attempt = int(row["attempt"])
            if row["state"] == IntentState.VERIFIED_NOT_SUBMITTED.value:
                attempt += 1
            now = utc_now()
            conn.execute(
                """
                UPDATE action_intents
                SET state=?,attempt=?,ambiguity_reason=NULL,observation_json=NULL,updated_at=?
                WHERE intent_id=?
                """,
                (IntentState.MAY_HAVE_SUBMITTED.value, attempt, now, intent_id),
            )
            conn.execute(
                "UPDATE outbox SET state='SUBMITTING',claimed_at=?,completed_at=NULL WHERE intent_id=?",
                (now, intent_id),
            )
            return dict(
                conn.execute("SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def block_intent(
        self, intent_id: str, *, reason: str, observation: Mapping[str, Any]
    ) -> dict[str, Any]:
        why = str(reason or "").strip()
        if not why or not isinstance(observation, Mapping):
            raise StoreInvariantError("INTENT_BLOCK_EVIDENCE_INVALID")
        with self._transaction() as conn:
            if conn.execute(
                """
                UPDATE action_intents
                SET state=?,ambiguity_reason=?,observation_json=?,updated_at=?
                WHERE intent_id=?
                """,
                (
                    IntentState.BLOCKED_AMBIGUOUS.value,
                    why,
                    canonical_json(dict(observation)),
                    utc_now(),
                    intent_id,
                ),
            ).rowcount != 1:
                raise StoreInvariantError("INTENT_NOT_FOUND")
            conn.execute("UPDATE outbox SET state='BLOCKED' WHERE intent_id=?", (intent_id,))
            return dict(
                conn.execute("SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def confirm_submitted(
        self,
        intent_id: str,
        *,
        conversation_url: str,
        remote_identity: str,
        observation: Mapping[str, Any],
    ) -> dict[str, Any]:
        url = str(conversation_url or "").strip()
        remote = str(remote_identity or "").strip()
        if not url or not remote:
            raise StoreInvariantError("SUBMIT_IDENTITY_MISSING")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT state FROM action_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None or row["state"] not in {
                IntentState.MAY_HAVE_SUBMITTED.value,
                IntentState.BLOCKED_AMBIGUOUS.value,
                IntentState.CONFIRMED_SUBMITTED.value,
            }:
                raise StoreInvariantError("INTENT_CONFIRM_STATE_INVALID")
            conn.execute(
                """
                UPDATE action_intents
                SET state=?,conversation_url=?,remote_identity=?,ambiguity_reason=NULL,
                    observation_json=?,updated_at=?
                WHERE intent_id=?
                """,
                (
                    IntentState.CONFIRMED_SUBMITTED.value,
                    url,
                    remote,
                    canonical_json(dict(observation)),
                    utc_now(),
                    intent_id,
                ),
            )
            conn.execute("UPDATE outbox SET state='RECONCILE' WHERE intent_id=?", (intent_id,))
            return dict(
                conn.execute("SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def mark_verified_not_submitted(
        self, intent_id: str, *, proof: str, observation: Mapping[str, Any]
    ) -> dict[str, Any]:
        positive_proof = str(proof or "").strip()
        if not positive_proof:
            raise StoreInvariantError("POSITIVE_NOT_SUBMITTED_PROOF_REQUIRED")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT state FROM action_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None or row["state"] not in {
                IntentState.MAY_HAVE_SUBMITTED.value,
                IntentState.BLOCKED_AMBIGUOUS.value,
            }:
                raise StoreInvariantError("INTENT_RETRY_PROOF_STATE_INVALID")
            conn.execute(
                """
                UPDATE action_intents
                SET state=?,ambiguity_reason=NULL,observation_json=?,updated_at=?
                WHERE intent_id=?
                """,
                (
                    IntentState.VERIFIED_NOT_SUBMITTED.value,
                    canonical_json({**dict(observation), "proof": positive_proof}),
                    utc_now(),
                    intent_id,
                ),
            )
            conn.execute("UPDATE outbox SET state='RETRYABLE' WHERE intent_id=?", (intent_id,))
            return dict(
                conn.execute("SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def capture_response(
        self,
        intent_id: str,
        *,
        response: Mapping[str, Any],
        conversation_url: str,
        remote_identity: str,
        observation: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(response, Mapping) or not response:
            raise StoreInvariantError("RESPONSE_EMPTY")
        url = str(conversation_url or "").strip()
        remote = str(remote_identity or "").strip()
        if not url or not remote:
            raise StoreInvariantError("RESPONSE_IDENTITY_MISSING")
        response_value = dict(response)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT state FROM action_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None or row["state"] not in {
                IntentState.MAY_HAVE_SUBMITTED.value,
                IntentState.CONFIRMED_SUBMITTED.value,
                IntentState.BLOCKED_AMBIGUOUS.value,
            }:
                raise StoreInvariantError("INTENT_RESPONSE_STATE_INVALID")
            conn.execute(
                """
                UPDATE action_intents
                SET state=?,conversation_url=?,remote_identity=?,response_json=?,
                    response_sha256=?,ambiguity_reason=NULL,observation_json=?,updated_at=?
                WHERE intent_id=?
                """,
                (
                    IntentState.RESPONSE_CAPTURED.value,
                    url,
                    remote,
                    canonical_json(response_value),
                    sha256_json(response_value),
                    canonical_json(dict(observation)),
                    utc_now(),
                    intent_id,
                ),
            )
            conn.execute(
                "UPDATE outbox SET state='PENDING_CLEANUP' WHERE intent_id=?", (intent_id,)
            )
            return dict(
                conn.execute("SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def capture_local_execution(
        self,
        intent_id: str,
        *,
        receipt: Mapping[str, Any],
        observation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a local process receipt without pretending it is a browser turn."""

        if not isinstance(receipt, Mapping) or not receipt:
            raise StoreInvariantError("LOCAL_EXECUTION_RECEIPT_EMPTY")
        if not isinstance(observation, Mapping) or not observation:
            raise StoreInvariantError("LOCAL_EXECUTION_OBSERVATION_EMPTY")
        response_value = dict(receipt)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT action_kind,state FROM action_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise StoreInvariantError("INTENT_NOT_FOUND")
            if row["action_kind"] != "LOCAL_EXECUTION":
                raise StoreInvariantError("INTENT_NOT_LOCAL_EXECUTION")
            if row["state"] != IntentState.MAY_HAVE_SUBMITTED.value:
                raise StoreInvariantError("LOCAL_EXECUTION_CAPTURE_STATE_INVALID")
            conn.execute(
                """
                UPDATE action_intents
                SET state=?,conversation_url=?,remote_identity=?,response_json=?,
                    response_sha256=?,ambiguity_reason=NULL,observation_json=?,updated_at=?
                WHERE intent_id=?
                """,
                (
                    IntentState.RESPONSE_CAPTURED.value,
                    f"local://{intent_id}",
                    "local-process",
                    canonical_json(response_value),
                    sha256_json(response_value),
                    canonical_json(dict(observation)),
                    utc_now(),
                    intent_id,
                ),
            )
            conn.execute(
                "UPDATE outbox SET state='PENDING_CLEANUP' WHERE intent_id=?",
                (intent_id,),
            )
            return dict(
                conn.execute("SELECT * FROM action_intents WHERE intent_id=?", (intent_id,)).fetchone()
            )

    def finalize_intent(self, intent_id: str) -> None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT state FROM action_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None or row["state"] != IntentState.RESPONSE_CAPTURED.value:
                raise StoreInvariantError("INTENT_NOT_RESPONSE_CAPTURED")
            conn.execute(
                "UPDATE outbox SET state='COMPLETED',completed_at=? WHERE intent_id=?",
                (utc_now(), intent_id),
            )

    def pending_intents(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT i.* FROM action_intents i
                JOIN outbox o ON o.intent_id=i.intent_id
                WHERE o.state <> 'COMPLETED'
                ORDER BY i.created_at,i.intent_id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def rebind_browser(
        self,
        project_id: str,
        channel: str,
        *,
        actor_id: str,
        conversation_url: str,
        predecessor_url: str | None,
        reason: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        actor = str(actor_id or "").strip()
        url = str(conversation_url or "").strip()
        why = str(reason or "").strip()
        if not actor or not url.startswith("https://chatgpt.com/c/") or not isinstance(evidence, Mapping) or not evidence:
            raise StoreInvariantError("BROWSER_BINDING_INVALID")
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM browser_bindings WHERE project_id=? AND channel=?",
                (project_id, channel),
            ).fetchone()
            now = utc_now()
            if existing is None:
                if predecessor_url is not None or not why:
                    raise StoreInvariantError("INITIAL_BINDING_PROVENANCE_INVALID")
                conn.execute(
                    """
                    INSERT INTO browser_bindings(
                        project_id,channel,actor_id,conversation_url,generation,
                        predecessor_url,rebind_reason,evidence_json,updated_at
                    ) VALUES(?,?,?,?,0,NULL,?,?,?)
                    """,
                    (project_id, channel, actor, url, why, canonical_json(dict(evidence)), now),
                )
            else:
                if existing["actor_id"] != actor:
                    raise StoreInvariantError("BROWSER_BINDING_ACTOR_CHANGED")
                if existing["conversation_url"] == url:
                    return dict(existing)
                if predecessor_url != existing["conversation_url"] or not why:
                    raise StoreInvariantError("REBIND_PROVENANCE_REQUIRED")
                conn.execute(
                    """
                    UPDATE browser_bindings
                    SET conversation_url=?,generation=generation+1,predecessor_url=?,
                        rebind_reason=?,evidence_json=?,updated_at=?
                    WHERE project_id=? AND channel=?
                    """,
                    (
                        url,
                        predecessor_url,
                        why,
                        canonical_json(dict(evidence)),
                        now,
                        project_id,
                        channel,
                    ),
                )
            return dict(
                conn.execute(
                    "SELECT * FROM browser_bindings WHERE project_id=? AND channel=?",
                    (project_id, channel),
                ).fetchone()
            )

    def get_browser_binding(
        self, project_id: str, channel: str
    ) -> dict[str, Any] | None:
        """Read one logical browser binding without changing its generation."""

        project = str(project_id or "").strip()
        name = str(channel or "").strip()
        if not project or not name:
            raise StoreInvariantError("BROWSER_BINDING_LOOKUP_INVALID")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM browser_bindings WHERE project_id=? AND channel=?",
                (project, name),
            ).fetchone()
            return dict(row) if row is not None else None

    def record_release_candidate(
        self,
        project_id: str,
        *,
        candidate_commit: str,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        commit = str(candidate_commit or "").strip().lower()
        if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
            raise StoreInvariantError("CANDIDATE_COMMIT_INVALID")
        if not isinstance(manifest, Mapping) or not manifest:
            raise StoreInvariantError("CANDIDATE_MANIFEST_INVALID")
        manifest_value = dict(manifest)
        manifest_text = canonical_json(manifest_value)
        manifest_hash = sha256_json(manifest_value)
        with self._transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM project_state WHERE project_id=?", (project_id,)
            ).fetchone() is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            existing = conn.execute(
                "SELECT * FROM release_candidates WHERE project_id=?", (project_id,)
            ).fetchone()
            if existing is not None:
                identity = (
                    str(existing["candidate_commit"]),
                    str(existing["manifest_sha256"]),
                )
                if identity != (commit, manifest_hash):
                    raise StoreInvariantError("RELEASE_CANDIDATE_IDENTITY_CONFLICT")
                return dict(existing)
            conn.execute(
                """
                INSERT INTO release_candidates(
                    project_id,candidate_commit,manifest_json,manifest_sha256
                ) VALUES(?,?,?,?)
                """,
                (project_id, commit, manifest_text, manifest_hash),
            )
            conn.execute(
                "UPDATE project_state SET completion_candidate_commit=?,updated_at=? WHERE project_id=?",
                (commit, utc_now(), project_id),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM release_candidates WHERE project_id=?", (project_id,)
                ).fetchone()
            )

    def record_evidence_receipt(
        self,
        project_id: str,
        evidence_ref: str,
        *,
        acceptance_id: str,
        result: str,
        candidate_commit: str,
        contract_sha256: str,
        artifact_sha256: str,
        command_or_action: str,
        observed_state: Mapping[str, Any],
        raw_output_reference: str,
        started_at: str,
        finished_at: str,
    ) -> dict[str, Any]:
        evidence = str(evidence_ref or "").strip()
        acceptance = str(acceptance_id or "").strip()
        outcome = str(result or "").strip().upper()
        commit = str(candidate_commit or "").strip().lower()
        contract_hash = str(contract_sha256 or "").strip().lower()
        artifact_hash = str(artifact_sha256 or "").strip().lower()
        command = str(command_or_action or "").strip()
        raw_ref = str(raw_output_reference or "").strip()
        if not evidence or not acceptance or outcome not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}:
            raise StoreInvariantError("EVIDENCE_IDENTITY_INVALID")
        if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
            raise StoreInvariantError("EVIDENCE_CANDIDATE_INVALID")
        for digest in (contract_hash, artifact_hash):
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise StoreInvariantError("EVIDENCE_DIGEST_INVALID")
        if not command or not raw_ref or not isinstance(observed_state, Mapping) or not observed_state:
            raise StoreInvariantError("EVIDENCE_OBSERVATION_INVALID")
        _parse_timestamp(started_at)
        _parse_timestamp(finished_at)
        if _parse_timestamp(finished_at) < _parse_timestamp(started_at):
            raise StoreInvariantError("EVIDENCE_TIME_REVERSED")
        identity = (
            project_id,
            acceptance,
            outcome,
            commit,
            contract_hash,
            artifact_hash,
            command,
            canonical_json(dict(observed_state)),
            raw_ref,
            started_at,
            finished_at,
        )
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM evidence_receipts WHERE evidence_ref=?", (evidence,)
            ).fetchone()
            if existing is not None:
                actual = tuple(existing[key] for key in (
                    "project_id", "acceptance_id", "result", "candidate_commit",
                    "contract_sha256", "artifact_sha256", "command_or_action",
                    "observed_state_json", "raw_output_reference", "started_at", "finished_at",
                ))
                if actual != identity:
                    raise StoreInvariantError("EVIDENCE_IDENTITY_CONFLICT")
                return dict(existing)
            if conn.execute(
                "SELECT 1 FROM contracts WHERE project_id=?", (project_id,)
            ).fetchone() is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            conn.execute(
                """
                INSERT INTO evidence_receipts(
                    evidence_ref,project_id,acceptance_id,result,candidate_commit,
                    contract_sha256,artifact_sha256,command_or_action,
                    observed_state_json,raw_output_reference,started_at,finished_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (evidence, *identity),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM evidence_receipts WHERE evidence_ref=?", (evidence,)
                ).fetchone()
            )

    def open_review(self, project_id: str, review_id: str, *, deadline: str) -> dict[str, Any]:
        review = str(review_id or "").strip()
        _parse_timestamp(deadline)
        if not review:
            raise StoreInvariantError("REVIEW_ID_EMPTY")
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM review_deadlines WHERE project_id=? AND review_id=?",
                (project_id, review),
            ).fetchone()
            if existing is not None:
                if str(existing["deadline"]) != deadline:
                    raise StoreInvariantError("REVIEW_IDENTITY_CONFLICT")
                return dict(existing)
            conn.execute(
                "INSERT INTO review_deadlines(project_id,review_id,deadline) VALUES(?,?,?)",
                (project_id, review, deadline),
            )
            return dict(conn.execute(
                "SELECT * FROM review_deadlines WHERE project_id=? AND review_id=?",
                (project_id, review),
            ).fetchone())

    def record_review_finding(
        self,
        project_id: str,
        review_id: str,
        finding_id: str,
        *,
        severity: str,
        blocking: bool,
        payload: Mapping[str, Any],
        observed_at: str,
    ) -> dict[str, Any]:
        finding = str(finding_id or "").strip()
        level = str(severity or "").strip().lower()
        if not finding or not level or not isinstance(payload, Mapping) or not payload:
            raise StoreInvariantError("REVIEW_FINDING_INVALID")
        observed = _parse_timestamp(observed_at)
        payload_text = canonical_json(dict(payload))
        with self._transaction() as conn:
            review = conn.execute(
                "SELECT * FROM review_deadlines WHERE project_id=? AND review_id=?",
                (project_id, review_id),
            ).fetchone()
            if review is None:
                raise StoreInvariantError("REVIEW_NOT_FOUND")
            existing = conn.execute(
                "SELECT * FROM review_findings WHERE project_id=? AND review_id=? AND finding_id=?",
                (project_id, review_id, finding),
            ).fetchone()
            deadline = _parse_timestamp(str(review["deadline"]))
            closed_at = _parse_timestamp(str(review["closed_at"])) if review["closed_at"] else None
            late = observed > deadline or (closed_at is not None and observed > closed_at)
            identity = (level, 1 if blocking else 0, payload_text, observed_at, 1 if late else 0)
            if existing is not None:
                actual = tuple(existing[key] for key in (
                    "severity", "blocking", "payload_json", "observed_at", "late"
                ))
                if actual != identity:
                    raise StoreInvariantError("REVIEW_FINDING_APPEND_ONLY_CONFLICT")
                return dict(existing)
            conn.execute(
                """
                INSERT INTO review_findings(
                    project_id,review_id,finding_id,severity,blocking,payload_json,
                    observed_at,late
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (project_id, review_id, finding, *identity),
            )
            if late and blocking:
                conn.execute(
                    "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        f"event-{uuid.uuid4().hex}",
                        project_id,
                        "LATE_BLOCKING_REVIEW_FINDING",
                        canonical_json({"review_id": review_id, "finding_id": finding}),
                        utc_now(),
                    ),
                )
            return dict(conn.execute(
                "SELECT * FROM review_findings WHERE project_id=? AND review_id=? AND finding_id=?",
                (project_id, review_id, finding),
            ).fetchone())

    def close_review(
        self, project_id: str, review_id: str, *, verdict: str, closed_at: str
    ) -> dict[str, Any]:
        result = str(verdict or "").strip().upper()
        if result not in {"PASS", "FAIL", "BLOCKED"}:
            raise StoreInvariantError("REVIEW_VERDICT_INVALID")
        _parse_timestamp(closed_at)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM review_deadlines WHERE project_id=? AND review_id=?",
                (project_id, review_id),
            ).fetchone()
            if row is None:
                raise StoreInvariantError("REVIEW_NOT_FOUND")
            if row["verdict"] is not None:
                if (str(row["verdict"]), str(row["closed_at"])) != (result, closed_at):
                    raise StoreInvariantError("REVIEW_VERDICT_IMMUTABLE")
                return dict(row)
            conn.execute(
                "UPDATE review_deadlines SET verdict=?,closed_at=? WHERE project_id=? AND review_id=?",
                (result, closed_at, project_id, review_id),
            )
            return dict(conn.execute(
                "SELECT * FROM review_deadlines WHERE project_id=? AND review_id=?",
                (project_id, review_id),
            ).fetchone())

    def get_review(self, project_id: str, review_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM review_deadlines WHERE project_id=? AND review_id=?",
                (project_id, review_id),
            ).fetchone()
            if row is None:
                raise StoreInvariantError("REVIEW_NOT_FOUND")
            result = dict(row)
            findings = conn.execute(
                "SELECT * FROM review_findings WHERE project_id=? AND review_id=? ORDER BY observed_at,finding_id",
                (project_id, review_id),
            ).fetchall()
            result["findings"] = [dict(finding) for finding in findings]
            result["late_finding_count"] = sum(int(finding["late"]) for finding in findings)
            return result


def _parse_timestamp(value: str) -> dt.datetime:
    text = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StoreInvariantError("TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise StoreInvariantError("TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    return parsed.astimezone(UTC)
