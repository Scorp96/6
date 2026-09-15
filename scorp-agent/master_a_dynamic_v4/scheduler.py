from __future__ import annotations

import dataclasses
import datetime as dt
import json
import secrets
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from .models import canonical_json, sha256_json
from .path_policy import PathPolicy
from .state_store import StateStore, StoreInvariantError, UTC, utc_now
from .work_result import WorkResultRejected, validate_work_result


class SchedulerError(RuntimeError):
    pass


class WorkerFenceError(SchedulerError):
    pass


@dataclasses.dataclass(frozen=True)
class AssignmentClaim:
    assignment_id: str
    project_id: str
    task_id: str
    worker_id: str
    slot_id: str
    lease_token: str
    master_epoch: int
    base_state_version: int
    objective_sha256: str
    resource_scope: tuple[str, ...]
    access_mode: str
    expires_at: str
    task_context: Mapping[str, Any] = dataclasses.field(default_factory=dict)


def _aware(value: dt.datetime | None) -> dt.datetime:
    current = value or dt.datetime.now(UTC)
    if current.tzinfo is None:
        raise SchedulerError("TIME_MUST_BE_TIMEZONE_AWARE")
    return current.astimezone(UTC)


def _timestamp(value: dt.datetime | None) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")


def _decode_task_context(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError) as exc:
        raise SchedulerError("TASK_CONTEXT_INVALID") from exc
    if not isinstance(value, Mapping):
        raise SchedulerError("TASK_CONTEXT_INVALID")
    return dict(value)


class Scheduler:
    def __init__(
        self,
        store: StateStore,
        project_id: str,
        path_policy: PathPolicy,
        *,
        max_workers: int = 2,
    ):
        self.store = store
        self.project_id = str(project_id or "").strip()
        self.path_policy = path_policy
        self.max_workers = int(max_workers)
        if not self.project_id:
            raise SchedulerError("PROJECT_ID_EMPTY")
        if self.max_workers != 2:
            raise SchedulerError("V4_DEFAULT_WORKER_CONCURRENCY_MUST_BE_TWO")

    def enqueue_graph(self, tasks: Sequence[Mapping[str, Any]]) -> None:
        if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)) or not tasks:
            raise SchedulerError("TASK_GRAPH_EMPTY")
        normalized: list[dict[str, Any]] = []
        ids: set[str] = set()
        for raw in tasks:
            if not isinstance(raw, Mapping):
                raise SchedulerError("TASK_NOT_MAPPING")
            task_id = str(raw.get("task_id") or "").strip()
            objective = str(raw.get("objective_sha256") or "").strip().lower()
            access_mode = str(raw.get("access_mode") or "write").strip().lower()
            dependencies = [str(value).strip() for value in raw.get("dependencies", [])]
            task_context = raw.get("task_context", {})
            if not isinstance(task_context, Mapping):
                raise SchedulerError("TASK_CONTEXT_INVALID")
            try:
                task_context_json = canonical_json(dict(task_context))
            except (TypeError, ValueError) as exc:
                raise SchedulerError("TASK_CONTEXT_INVALID") from exc
            if len(task_context_json.encode("utf-8")) > 64 * 1024:
                raise SchedulerError("TASK_CONTEXT_TOO_LARGE")
            if not task_id or task_id in ids:
                raise SchedulerError("TASK_ID_INVALID_OR_DUPLICATE")
            if len(objective) != 64 or any(ch not in "0123456789abcdef" for ch in objective):
                raise SchedulerError("TASK_OBJECTIVE_SHA256_INVALID")
            if task_id in dependencies or len(dependencies) != len(set(dependencies)):
                raise SchedulerError("TASK_DEPENDENCIES_INVALID")
            scope = self.path_policy.authorize(raw.get("resource_scope", []), access_mode)
            ids.add(task_id)
            normalized.append(
                {
                    "task_id": task_id,
                    "objective_sha256": objective,
                    "resource_scope": scope,
                    "task_context_json": task_context_json,
                    "access_mode": access_mode,
                    "dependencies": dependencies,
                    "required": 1 if raw.get("required", True) else 0,
                }
            )
        for task in normalized:
            if any(dependency not in ids for dependency in task["dependencies"]):
                raise SchedulerError("TASK_DEPENDENCY_NOT_IN_GRAPH")
        graph = {task["task_id"]: set(task["dependencies"]) for task in normalized}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise SchedulerError("TASK_DEPENDENCY_CYCLE")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

        now = utc_now()
        with self.store._transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM project_state WHERE project_id=?", (self.project_id,)
            ).fetchone() is None:
                raise SchedulerError("PROJECT_NOT_FOUND")
            control = conn.execute(
                "SELECT operator_state FROM operator_controls WHERE project_id=?",
                (self.project_id,),
            ).fetchone()
            if control is None:
                raise SchedulerError("OPERATOR_CONTROL_NOT_FOUND")
            if str(control["operator_state"]) not in {"ACTIVE", "RUNNING"}:
                raise SchedulerError("OPERATOR_STATE_FENCED")
            for task in normalized:
                identity = {
                    "objective_sha256": task["objective_sha256"],
                    "resource_scope_json": canonical_json(list(task["resource_scope"])),
                    "task_context_json": task["task_context_json"],
                    "access_mode": task["access_mode"],
                    "required": task["required"],
                }
                existing = conn.execute(
                    "SELECT * FROM task_nodes WHERE project_id=? AND task_id=?",
                    (self.project_id, task["task_id"]),
                ).fetchone()
                if existing is not None:
                    actual = {key: existing[key] for key in identity}
                    if actual != identity:
                        raise SchedulerError("TASK_IDENTITY_CONFLICT")
                    continue
                conn.execute(
                    """
                    INSERT INTO task_nodes(
                        project_id,task_id,objective_sha256,resource_scope_json,
                        task_context_json,access_mode,required,state,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,'QUEUED',?,?)
                    """,
                    (
                        self.project_id,
                        task["task_id"],
                        task["objective_sha256"],
                        identity["resource_scope_json"],
                        identity["task_context_json"],
                        task["access_mode"],
                        task["required"],
                        now,
                        now,
                    ),
                )
            for task in normalized:
                for dependency in task["dependencies"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO task_dependencies(project_id,task_id,depends_on_task_id) VALUES(?,?,?)",
                        (self.project_id, task["task_id"], dependency),
                    )

    @staticmethod
    def _scopes_conflict(
        first_scope: set[str], first_mode: str, second_scope: set[str], second_mode: str
    ) -> bool:
        return bool(first_scope & second_scope) and (first_mode == "write" or second_mode == "write")

    def recover_expired_leases(self, *, now: dt.datetime | None = None) -> int:
        stamp = _timestamp(now)
        with self.store._transaction() as conn:
            expired = conn.execute(
                """
                SELECT l.*,a.task_id,a.state AS assignment_state
                FROM leases l JOIN assignments a ON a.assignment_id=l.assignment_id
                WHERE l.project_id=? AND l.state='ACTIVE' AND l.expires_at < ?
                ORDER BY l.slot_id
                """,
                (self.project_id, stamp),
            ).fetchall()
            for lease in expired:
                conn.execute(
                    "UPDATE leases SET state='EXPIRED',released_at=? WHERE lease_token=?",
                    (stamp, lease["lease_token"]),
                )
                conn.execute(
                    "UPDATE assignments SET state='FENCED',updated_at=? WHERE assignment_id=?",
                    (stamp, lease["assignment_id"]),
                )
                if str(lease["assignment_state"]) == "ACTIVE":
                    conn.execute(
                        "UPDATE task_nodes SET state='QUEUED',updated_at=? WHERE project_id=? AND task_id=?",
                        (stamp, self.project_id, lease["task_id"]),
                    )
                conn.execute(
                    "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        f"event-{uuid.uuid4().hex}",
                        self.project_id,
                        "WORKER_LEASE_EXPIRED",
                        canonical_json(
                            {
                                "assignment_id": lease["assignment_id"],
                                "lease_token_sha256": sha256_json(lease["lease_token"]),
                            }
                        ),
                        stamp,
                    ),
                )
            return len(expired)

    def load_active_claims(
        self, *, master_epoch: int, now: dt.datetime | None = None
    ) -> list[AssignmentClaim]:
        """Rehydrate durable Worker claims after a coordinator restart.

        Assignment identity, lease token and the graph's base state version are
        read from SQLite. No new lease is created and no browser action is
        issued. Expired leases are fenced first, so callers can safely resume
        only the assignments still owned by the current epoch.
        """
        current = _aware(now)
        self.recover_expired_leases(now=current)
        with self.store._connection() as conn:
            state = conn.execute(
                "SELECT master_epoch,state_version,status FROM project_state WHERE project_id=?",
                (self.project_id,),
            ).fetchone()
            if state is None:
                raise SchedulerError("PROJECT_NOT_FOUND")
            if int(state["master_epoch"]) != int(master_epoch):
                raise WorkerFenceError("MASTER_EPOCH_FENCED")
            # A successful project.resume records lifecycle status RUNNING;
            # the explicit operator-state gate below still fences paused or
            # superseded work.
            if str(state["status"]) not in {"ACTIVE", "RUNNING"}:
                return []
            rows = conn.execute(
                """
                SELECT a.*,t.task_context_json,l.expires_at,l.state AS lease_state
                FROM assignments a
                JOIN task_nodes t ON t.project_id=a.project_id AND t.task_id=a.task_id
                JOIN leases l ON l.assignment_id=a.assignment_id
                WHERE a.project_id=? AND a.master_epoch=?
                  AND a.state='ACTIVE' AND l.state='ACTIVE'
                ORDER BY a.slot_id,a.assignment_id
                """,
                (self.project_id, int(master_epoch)),
            ).fetchall()
            claims: list[AssignmentClaim] = []
            stamp = _timestamp(current)
            for row in rows:
                if str(row["expires_at"]) <= stamp:
                    continue
                claims.append(
                    AssignmentClaim(
                        assignment_id=str(row["assignment_id"]),
                        project_id=self.project_id,
                        task_id=str(row["task_id"]),
                        worker_id=str(row["worker_id"]),
                        slot_id=str(row["slot_id"]),
                        lease_token=str(row["lease_token"]),
                        master_epoch=int(row["master_epoch"]),
                        base_state_version=int(row["base_state_version"]),
                        objective_sha256=str(row["objective_sha256"]),
                        resource_scope=tuple(sorted(json.loads(str(row["resource_scope_json"]))),),
                        access_mode=str(row["access_mode"]),
                        expires_at=str(row["expires_at"]),
                        task_context=_decode_task_context(row["task_context_json"]),
                    )
                )
            return claims

    def claim_runnable(
        self,
        *,
        master_epoch: int,
        now: dt.datetime | None = None,
        lease_seconds: int = 900,
        limit: int = 2,
    ) -> list[AssignmentClaim]:
        current = _aware(now)
        if lease_seconds < 1:
            raise SchedulerError("LEASE_SECONDS_INVALID")
        try:
            requested_limit = int(limit)
        except (TypeError, ValueError):
            raise SchedulerError("V4_WORKER_LIMIT_INVALID")
        if requested_limit > self.max_workers:
            raise SchedulerError("V4_WORKER_LIMIT_INVALID")
        if requested_limit < 1:
            return []
        self.recover_expired_leases(now=current)
        stamp = _timestamp(current)
        expires = _timestamp(current + dt.timedelta(seconds=lease_seconds))
        claims: list[AssignmentClaim] = []
        with self.store._transaction() as conn:
            state = conn.execute(
                "SELECT master_epoch,state_version,status FROM project_state WHERE project_id=?",
                (self.project_id,),
            ).fetchone()
            if state is None:
                raise SchedulerError("PROJECT_NOT_FOUND")
            if int(state["master_epoch"]) != int(master_epoch):
                raise WorkerFenceError("MASTER_EPOCH_FENCED")
            if str(state["status"]) not in {"ACTIVE", "RUNNING"}:
                return []
            control = conn.execute(
                "SELECT operator_state FROM operator_controls WHERE project_id=?",
                (self.project_id,),
            ).fetchone()
            if control is None:
                raise SchedulerError("OPERATOR_CONTROL_NOT_FOUND")
            if str(control["operator_state"]) not in {"ACTIVE", "RUNNING"}:
                return []
            # New ordinary assignments must not bypass the arbiter's
            # reconciliation fence.  A MAY_HAVE_SUBMITTED or
            # BLOCKED_AMBIGUOUS browser intent means the external side effect
            # is unresolved; only the reconciliation path may proceed.
            ambiguous = conn.execute(
                """
                SELECT 1 FROM action_intents
                WHERE project_id=? AND state IN ('MAY_HAVE_SUBMITTED','BLOCKED_AMBIGUOUS')
                LIMIT 1
                """,
                (self.project_id,),
            ).fetchone()
            if ambiguous is not None:
                return []

            active_rows = conn.execute(
                """
                SELECT l.slot_id,a.resource_scope_json,a.access_mode
                FROM leases l JOIN assignments a ON a.assignment_id=l.assignment_id
                WHERE l.project_id=? AND l.state='ACTIVE'
                """,
                (self.project_id,),
            ).fetchall()
            active: list[tuple[set[str], str]] = [
                (set(json.loads(row["resource_scope_json"])), str(row["access_mode"]))
                for row in active_rows
            ]
            occupied = {str(row["slot_id"]) for row in active_rows}
            free_slots = [
                f"worker-slot-{number}"
                for number in range(1, self.max_workers + 1)
                if f"worker-slot-{number}" not in occupied
            ]
            capacity = min(requested_limit, len(free_slots))
            if capacity == 0:
                return []

            tasks = conn.execute(
                "SELECT * FROM task_nodes WHERE project_id=? AND state='QUEUED' ORDER BY task_id",
                (self.project_id,),
            ).fetchall()
            for task in tasks:
                blocked_dependency = conn.execute(
                    """
                    SELECT 1 FROM task_dependencies d
                    LEFT JOIN task_nodes dependency
                      ON dependency.project_id=d.project_id
                     AND dependency.task_id=d.depends_on_task_id
                    WHERE d.project_id=? AND d.task_id=?
                      AND (dependency.state IS NULL OR dependency.state NOT IN ('VERIFIED','ACCEPTED'))
                    LIMIT 1
                    """,
                    (self.project_id, task["task_id"]),
                ).fetchone()
                if blocked_dependency is not None:
                    continue
                scope = set(json.loads(task["resource_scope_json"]))
                mode = str(task["access_mode"])
                if any(self._scopes_conflict(scope, mode, other, other_mode) for other, other_mode in active):
                    continue
                slot_id = free_slots[len(claims)]
                assignment_id = f"assignment-{uuid.uuid4().hex}"
                worker_id = f"worker-{uuid.uuid4().hex}"
                lease_token = secrets.token_urlsafe(32)
                conn.execute(
                    """
                    INSERT INTO assignments(
                        assignment_id,project_id,task_id,worker_id,slot_id,master_epoch,
                        base_state_version,lease_token,objective_sha256,resource_scope_json,access_mode,state,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?)
                    """,
                    (
                        assignment_id,
                        self.project_id,
                        task["task_id"],
                        worker_id,
                        slot_id,
                        int(master_epoch),
                        int(state["state_version"]),
                        lease_token,
                        task["objective_sha256"],
                        task["resource_scope_json"],
                        mode,
                        stamp,
                        stamp,
                    ),
                )
                conn.execute(
                    "INSERT INTO leases(lease_token,assignment_id,project_id,slot_id,master_epoch,state,acquired_at,expires_at) VALUES(?,?,?,?,?,'ACTIVE',?,?)",
                    (
                        lease_token,
                        assignment_id,
                        self.project_id,
                        slot_id,
                        int(master_epoch),
                        stamp,
                        expires,
                    ),
                )
                conn.execute(
                    "UPDATE task_nodes SET state='RUNNING',updated_at=? WHERE project_id=? AND task_id=?",
                    (stamp, self.project_id, task["task_id"]),
                )
                claims.append(
                    AssignmentClaim(
                        assignment_id=assignment_id,
                        project_id=self.project_id,
                        task_id=str(task["task_id"]),
                        worker_id=worker_id,
                        slot_id=slot_id,
                        lease_token=lease_token,
                        master_epoch=int(master_epoch),
                        base_state_version=int(state["state_version"]),
                        objective_sha256=str(task["objective_sha256"]),
                        resource_scope=tuple(sorted(scope)),
                        access_mode=mode,
                        expires_at=expires,
                        task_context=_decode_task_context(task["task_context_json"]),
                    )
                )
                active.append((scope, mode))
                if len(claims) >= capacity:
                    break
        return claims

    def record_work_result(
        self,
        claim: AssignmentClaim,
        *,
        payload: Mapping[str, Any],
        now: dt.datetime | None = None,
    ) -> str:
        """Admit one version-bound structured Worker result.

        The project state version is checked before the result enters the
        candidate ledger.  A Master transition after assignment creation makes
        the result stale instead of allowing it to update the task silently.
        """

        if not isinstance(claim, AssignmentClaim):
            raise WorkerFenceError("ASSIGNMENT_CLAIM_INVALID")
        state = self.store.get_project_state(self.project_id)
        if int(state["state_version"]) != int(claim.base_state_version):
            raise WorkerFenceError("TASK_GRAPH_VERSION_FENCED")
        try:
            normalized = validate_work_result(
                payload,
                project_id=self.project_id,
                assignment_id=claim.assignment_id,
                task_id=claim.task_id,
                objective_sha256=claim.objective_sha256,
                base_state_version=claim.base_state_version,
            )
        except WorkResultRejected as exc:
            raise SchedulerError(str(exc)) from exc
        if normalized["worker_id"] != claim.worker_id:
            raise WorkerFenceError("WORKER_ID_MISMATCH")
        return self.record_candidate(
            claim.assignment_id,
            lease_token=claim.lease_token,
            master_epoch=claim.master_epoch,
            kind="WORK_RESULT",
            payload=normalized,
            now=now,
        )

    def record_candidate(
        self,
        assignment_id: str,
        *,
        lease_token: str,
        master_epoch: int,
        kind: str,
        payload: Mapping[str, Any],
        now: dt.datetime | None = None,
    ) -> str:
        result_kind = str(kind or "").upper()
        if result_kind not in {"HANDOFF", "BLOCKER", "WORK_RESULT"} or not isinstance(payload, Mapping):
            raise SchedulerError("WORKER_RESULT_INVALID")
        stamp = _timestamp(now)
        with self.store._transaction() as conn:
            row = conn.execute(
                """
                SELECT a.*,l.state AS lease_state,l.expires_at,s.master_epoch AS current_epoch
                FROM assignments a
                JOIN leases l ON l.assignment_id=a.assignment_id
                JOIN project_state s ON s.project_id=a.project_id
                WHERE a.assignment_id=? AND a.project_id=?
                """,
                (assignment_id, self.project_id),
            ).fetchone()
            if row is None:
                raise WorkerFenceError("ASSIGNMENT_NOT_FOUND")
            if (
                str(row["lease_token"]) != str(lease_token)
                or str(row["lease_state"]) != "ACTIVE"
                or int(row["master_epoch"]) != int(master_epoch)
                or int(row["current_epoch"]) != int(master_epoch)
                or str(row["expires_at"]) < stamp
            ):
                raise WorkerFenceError("WORKER_FENCED")
            payload_value = dict(payload)
            payload_hash = sha256_json(payload_value)
            result_id = f"result-{assignment_id}"
            existing = conn.execute(
                "SELECT payload_sha256,result_kind FROM candidate_results WHERE result_id=?",
                (result_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_sha256"]) == payload_hash and str(existing["result_kind"]) == result_kind:
                    return result_id
                raise WorkerFenceError("WORKER_RESULT_CONFLICT")
            conn.execute(
                """
                INSERT INTO candidate_results(
                    result_id,project_id,assignment_id,lease_token,master_epoch,
                    result_kind,payload_json,payload_sha256,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    result_id,
                    self.project_id,
                    assignment_id,
                    lease_token,
                    int(master_epoch),
                    result_kind,
                    canonical_json(payload_value),
                    payload_hash,
                    stamp,
                ),
            )
            conn.execute(
                "UPDATE assignments SET state='RESULT_RECEIVED',updated_at=? WHERE assignment_id=?",
                (stamp, assignment_id),
            )
            return result_id

    def verify_candidate(
        self, result_id: str, *, result_sha256: str, now: dt.datetime | None = None
    ) -> None:
        digest = str(result_sha256 or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise SchedulerError("RESULT_SHA256_INVALID")
        stamp = _timestamp(now)
        with self.store._transaction() as conn:
            row = conn.execute(
                """
                SELECT r.*,a.task_id,a.state AS assignment_state
                FROM candidate_results r JOIN assignments a ON a.assignment_id=r.assignment_id
                WHERE r.result_id=? AND r.project_id=?
                """,
                (result_id, self.project_id),
            ).fetchone()
            if row is None or str(row["assignment_state"]) != "RESULT_RECEIVED":
                raise SchedulerError("CANDIDATE_NOT_READY")
            try:
                payload = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError):
                raise SchedulerError("RESULT_PAYLOAD_INVALID")
            if not isinstance(payload, dict):
                raise SchedulerError("RESULT_PAYLOAD_INVALID")
            if "result_sha256" not in payload:
                raise SchedulerError("RESULT_IDENTITY_MISSING")
            declared = str(payload.get("result_sha256") or "").strip().lower()
            if len(declared) != 64 or any(ch not in "0123456789abcdef" for ch in declared):
                raise SchedulerError("RESULT_SHA256_INVALID")
            if digest != declared:
                raise SchedulerError("RESULT_SHA256_MISMATCH")
            result_kind = str(row["result_kind"])
            verification_state = "VERIFIED" if result_kind == "WORK_RESULT" else "VERIFIED_LEGACY"
            if result_kind == "WORK_RESULT":
                state = conn.execute(
                    "SELECT state_version FROM project_state WHERE project_id=?",
                    (self.project_id,),
                ).fetchone()
                try:
                    validate_work_result(
                        payload,
                        project_id=self.project_id,
                        assignment_id=str(row["assignment_id"]),
                        task_id=str(row["task_id"]),
                        objective_sha256=str(
                            conn.execute(
                                "SELECT objective_sha256 FROM assignments WHERE assignment_id=?",
                                (row["assignment_id"],),
                            ).fetchone()[0]
                        ),
                        base_state_version=int(state["state_version"]),
                    )
                except (WorkResultRejected, TypeError, ValueError) as exc:
                    raise SchedulerError(str(exc)) from exc
            conn.execute(
                "UPDATE candidate_results SET verification_state=?,verified_result_sha256=?,verified_at=? WHERE result_id=?",
                (verification_state, digest, stamp, result_id),
            )
            task_state = "ACCEPTED" if result_kind == "WORK_RESULT" else "VERIFIED"
            conn.execute(
                "UPDATE task_nodes SET state=?,result_sha256=?,updated_at=? WHERE project_id=? AND task_id=?",
                (task_state, digest, stamp, self.project_id, row["task_id"]),
            )
            conn.execute(
                "UPDATE assignments SET state='RETIRED',updated_at=? WHERE assignment_id=?",
                (stamp, row["assignment_id"]),
            )
            conn.execute(
                "UPDATE leases SET state='RELEASED',released_at=? WHERE assignment_id=?",
                (stamp, row["assignment_id"]),
            )

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT * FROM task_nodes WHERE project_id=? AND task_id=?",
                (self.project_id, task_id),
            ).fetchone()
            if row is None:
                raise SchedulerError("TASK_NOT_FOUND")
            return dict(row)

    def get_assignment(self, assignment_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT * FROM assignments WHERE project_id=? AND assignment_id=?",
                (self.project_id, assignment_id),
            ).fetchone()
            if row is None:
                raise SchedulerError("ASSIGNMENT_NOT_FOUND")
            return dict(row)
