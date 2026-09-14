"""Bounded command service for the local runtime control boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .operator_control import OperatorControlService
from .runtime_protocol import RuntimeRequest, build_response
from .state_store import StateStore, StoreInvariantError


class RuntimeCommandService:
    """Execute only the registered read commands.

    Mutation handling lives in :mod:`operator_control`; keeping reads here
    makes it obvious that a status request cannot create a lease or touch a
    browser. Every query is bounded in SQL and returns JSON-compatible data.
    """

    def __init__(
        self,
        store: StateStore,
        project_id: str | None = None,
        actor_id: str | None = None,
        *,
        daemon_epoch: int | None = None,
        actor: str | None = None,
    ):
        self.store = store
        self.project_id = str(project_id or "").strip()
        self.actor = str(actor_id or actor or "runtime")
        self.actor_id = self.actor
        if daemon_epoch is None and self.project_id:
            with self.store._connection() as conn:
                row = conn.execute(
                    "SELECT daemon_epoch FROM daemon_leases WHERE project_id=?",
                    (self.project_id,),
                ).fetchone()
            daemon_epoch = int(row[0]) if row is not None else 0
        self.daemon_epoch = int(daemon_epoch or 0)
        self.operator = OperatorControlService(
            store, daemon_epoch=self.daemon_epoch, actor=self.actor
        )

    def execute(self, request: RuntimeRequest) -> dict[str, Any]:
        try:
            if self.project_id and request.project_id != self.project_id:
                return build_response(
                    request,
                    status="REJECTED",
                    daemon_epoch=self.daemon_epoch,
                    error={"code": "PROJECT_SCOPE_MISMATCH"},
                )
            if request.is_mutation:
                return self.operator.execute(request)
            if request.command == "runtime.status":
                result = self._runtime_status(request.project_id)
            elif request.command == "project.status":
                result = self._project_status(request.project_id)
            elif request.command == "master.status":
                result = self._master_status(request.project_id)
            elif request.command == "evidence.query":
                result = self._evidence_query(request.project_id, request.payload)
            else:
                return build_response(
                    request,
                    status="REJECTED",
                    daemon_epoch=self.daemon_epoch,
                    error={"code": "COMMAND_NOT_READ_HANDLER"},
                )
            state_version = int(self.store.get_project_state(request.project_id)["state_version"])
            return build_response(
                request,
                status="OK",
                daemon_epoch=self.daemon_epoch,
                state_version=state_version,
                result=result,
            )
        except (StoreInvariantError, ValueError) as exc:
            return build_response(
                request,
                status="REJECTED",
                daemon_epoch=self.daemon_epoch,
                error={"code": str(exc).split(":", 1)[0], "detail": str(exc)},
            )

    def _control(self, project_id: str) -> dict[str, Any]:
        return self.store.get_operator_control(project_id)

    def _observation(self, project_id: str) -> dict[str, Any]:
        return self.store.get_runtime_observation(project_id)

    def _project_status(self, project_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            project = conn.execute(
                "SELECT project_id,state_version,master_epoch,status,phase,completion_candidate_commit,updated_at FROM project_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise StoreInvariantError("PROJECT_NOT_FOUND")
            daemon = conn.execute(
                "SELECT daemon_epoch,owner_id,heartbeat_at,lease_until FROM daemon_leases WHERE project_id=?",
                (project_id,),
            ).fetchone()
            return {
                "project": dict(project),
                "operator": self._control(project_id),
                "observation": self._observation(project_id),
                "daemon": dict(daemon) if daemon is not None else None,
            }

    def _runtime_status(self, project_id: str) -> dict[str, Any]:
        snapshot = self._project_status(project_id)
        with self.store._connection() as conn:
            active_workers = int(conn.execute(
                "SELECT COUNT(*) FROM leases WHERE project_id=? AND state='ACTIVE'",
                (project_id,),
            ).fetchone()[0])
            queued = int(conn.execute(
                "SELECT COUNT(*) FROM task_nodes WHERE project_id=? AND state='QUEUED'",
                (project_id,),
            ).fetchone()[0])
            running = int(conn.execute(
                "SELECT COUNT(*) FROM task_nodes WHERE project_id=? AND state IN ('RUNNING','ASSIGNED')",
                (project_id,),
            ).fetchone()[0])
            ambiguous = int(conn.execute(
                "SELECT COUNT(*) FROM action_intents WHERE project_id=? AND state IN ('MAY_HAVE_SUBMITTED','BLOCKED_AMBIGUOUS')",
                (project_id,),
            ).fetchone()[0])
            pending_results = int(conn.execute(
                "SELECT COUNT(*) FROM candidate_results WHERE project_id=? AND verification_state='PENDING'",
                (project_id,),
            ).fetchone()[0])
        master = self._master_status(project_id)
        return {
            "project_id": project_id,
            "project": snapshot["project"],
            "operator": snapshot["operator"],
            "observation": snapshot["observation"],
            "daemon": snapshot["daemon"],
            "master": master["master"],
            "last_decision": master["last_decision"],
            "workers": {"active": active_workers, "capacity": 2, "free": max(0, 2 - active_workers)},
            "tasks": {"queued": queued, "running": running},
            "reconciliation": {"ambiguous_intents": ambiguous, "pending_results": pending_results},
        }

    def _master_status(self, project_id: str) -> dict[str, Any]:
        with self.store._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM master_sessions WHERE project_id=?
                ORDER BY CASE WHEN state='ACTIVE' THEN 0 ELSE 1 END, heartbeat_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            pending_results = int(conn.execute(
                "SELECT COUNT(*) FROM candidate_results WHERE project_id=? AND verification_state='PENDING'",
                (project_id,),
            ).fetchone()[0])
            ambiguous = int(conn.execute(
                "SELECT COUNT(*) FROM action_intents WHERE project_id=? AND state IN ('MAY_HAVE_SUBMITTED','BLOCKED_AMBIGUOUS')",
                (project_id,),
            ).fetchone()[0])
            decision = conn.execute(
                "SELECT payload_json FROM events WHERE project_id=? AND kind='ACTIVATION_DECISION' ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return {
            "project_id": project_id,
            "master": dict(row) if row is not None else None,
            "pending_results": pending_results,
            "pending_reconciliation": ambiguous,
            "last_decision": json.loads(str(decision[0])) if decision is not None else None,
            "observation": self._observation(project_id),
        }

    def _evidence_query(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise StoreInvariantError("EVIDENCE_FILTER_OBJECT_REQUIRED")
        try:
            limit = int(payload.get("limit", 50))
        except (TypeError, ValueError) as exc:
            raise StoreInvariantError("EVIDENCE_LIMIT_INVALID") from exc
        if limit < 1 or limit > 100:
            raise StoreInvariantError("EVIDENCE_LIMIT_INVALID")
        allowed = {
            "limit", "assignment_id", "intent_id", "receipt_id",
            "since", "until", "from_utc", "to_utc",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise StoreInvariantError("EVIDENCE_FILTER_UNKNOWN")
        assignment_filter = str(payload.get("assignment_id") or "")
        intent_filter = str(payload.get("intent_id") or "")
        receipt_filter = str(payload.get("receipt_id") or "")
        since = str(payload.get("from_utc") or payload.get("since") or "")
        until = str(payload.get("to_utc") or payload.get("until") or "")

        def matches(item: Mapping[str, Any], *, timestamp_key: str) -> bool:
            timestamp = str(item.get(timestamp_key) or "")
            if since and timestamp < since:
                return False
            if until and timestamp > until:
                return False
            if receipt_filter and str(item.get("receipt_id") or "") != receipt_filter:
                return False
            if intent_filter and str(item.get("intent_id") or "") != intent_filter:
                return False
            if assignment_filter and str(item.get("assignment_id") or "") != assignment_filter:
                return False
            return True

        items: list[dict[str, Any]] = []
        with self.store._connection() as conn:
            evidence = conn.execute(
                "SELECT evidence_ref,acceptance_id,result,candidate_commit,artifact_sha256,command_or_action,started_at,finished_at,observed_state_json FROM evidence_receipts WHERE project_id=? ORDER BY finished_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            for row in evidence:
                item = dict(row)
                item["kind"] = "evidence_receipt"
                item["observed_state"] = json.loads(str(item.pop("observed_state_json")))
                if matches(item, timestamp_key="finished_at"):
                    items.append(item)
            receipts = conn.execute(
                "SELECT receipt_id,request_id,command,status,reason,created_at,response_json FROM runtime_command_receipts WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            for row in receipts:
                item = dict(row)
                item["kind"] = "runtime_command_receipt"
                item["response"] = json.loads(str(item.pop("response_json")))
                if matches(item, timestamp_key="created_at"):
                    items.append(item)
            intents = conn.execute(
                "SELECT intent_id,action_kind,state,ambiguity_reason,created_at,updated_at,payload_json FROM action_intents WHERE project_id=? ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            for row in intents:
                item = dict(row)
                item["kind"] = "action_intent"
                raw_payload = json.loads(str(item.pop("payload_json")))
                item["payload"] = raw_payload
                if not matches(item, timestamp_key="updated_at"):
                    continue
                if assignment_filter and str(raw_payload.get("assignment_id") or "") != assignment_filter:
                    continue
                if intent_filter and item["intent_id"] != intent_filter:
                    continue
                items.append(item)
            results = conn.execute(
                "SELECT result_id,assignment_id,master_epoch,verification_state,created_at,verified_at,payload_json FROM candidate_results WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            for row in results:
                item = dict(row)
                item["kind"] = "candidate_result"
                item["payload"] = json.loads(str(item.pop("payload_json")))
                if matches(item, timestamp_key="verified_at" if item.get("verified_at") else "created_at"):
                    if not assignment_filter or str(item.get("assignment_id") or "") == assignment_filter:
                        items.append(item)
        items.sort(key=lambda value: str(value.get("finished_at") or value.get("created_at") or ""), reverse=True)
        return {"project_id": project_id, "limit": limit, "items": items[:limit]}
