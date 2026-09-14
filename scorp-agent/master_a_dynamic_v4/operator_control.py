"""Durable operator fences for pause, resume, cancel, and supersede."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from typing import Any

from .models import canonical_json
from .runtime_protocol import RuntimeRequest, build_response
from .state_store import StateStore, StoreInvariantError, utc_now


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class OperatorControlService:
    """Apply operator mutations in the same SQLite transaction as their receipt."""

    def __init__(self, store: StateStore, *, daemon_epoch: int, actor: str = "operator"):
        self.store = store
        self.daemon_epoch = int(daemon_epoch)
        self.actor = str(actor or "operator")

    def _actor_for_request(self, request: RuntimeRequest) -> str:
        # ``runtime`` is the backwards-compatible protocol default.  A
        # service-level actor remains authoritative for legacy callers that
        # omit the new envelope actor; an explicit non-default actor is
        # preserved in the durable receipt.
        return request.actor if request.actor != "runtime" else self.actor

    def execute(self, request: RuntimeRequest) -> dict[str, Any]:
        if not request.is_mutation:
            return build_response(
                request,
                status="REJECTED",
                daemon_epoch=self.daemon_epoch,
                error={"code": "COMMAND_NOT_MUTATION"},
            )

        receipt_id = f"receipt-{request.request_id}"
        try:
            with self.store._transaction() as conn:
                existing = conn.execute(
                    "SELECT * FROM runtime_command_receipts WHERE request_id=?",
                    (request.request_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["command"]) != request.command
                        or str(existing["payload_sha256"]) != request.payload_sha256
                    ):
                        return build_response(
                            request,
                            status="REJECTED",
                            daemon_epoch=self.daemon_epoch,
                            error={"code": "RUNTIME_RECEIPT_IDEMPOTENCY_CONFLICT"},
                        )
                    return json.loads(str(existing["response_json"]))

                now = utc_now()
                state = conn.execute(
                    "SELECT * FROM project_state WHERE project_id=?", (request.project_id,)
                ).fetchone()
                if state is None:
                    return self._reject_and_record(
                        conn, request, receipt_id, None, None, "PROJECT_NOT_FOUND", now
                    )
                daemon = conn.execute(
                    "SELECT * FROM daemon_leases WHERE project_id=?", (request.project_id,)
                ).fetchone()
                control = conn.execute(
                    "SELECT * FROM operator_controls WHERE project_id=?", (request.project_id,)
                ).fetchone()
                if daemon is None or int(daemon["daemon_epoch"]) != int(request.expected_daemon_epoch):
                    return self._reject_and_record(
                        conn, request, receipt_id, state, control, "DAEMON_EPOCH_CONFLICT", now
                    )
                if int(request.expected_daemon_epoch) != self.daemon_epoch:
                    return self._reject_and_record(
                        conn, request, receipt_id, state, control, "DAEMON_EPOCH_CONFLICT", now
                    )
                if int(state["state_version"]) != int(request.expected_state_version):
                    return self._reject_and_record(
                        conn, request, receipt_id, state, control, "STATE_VERSION_CONFLICT", now
                    )
                if control is None:
                    return self._reject_and_record(
                        conn, request, receipt_id, state, control, "OPERATOR_CONTROL_NOT_FOUND", now
                    )
                if request.expected_master_epoch is not None and int(state["master_epoch"]) != int(request.expected_master_epoch):
                    return self._reject_and_record(
                        conn, request, receipt_id, state, control, "MASTER_EPOCH_CONFLICT", now
                    )
                if request.expected_generation is not None and int(control["operator_generation"]) != int(request.expected_generation):
                    return self._reject_and_record(
                        conn, request, receipt_id, state, control, "GENERATION_CONFLICT", now
                    )
                if request.command == "project.supersede":
                    objective_sha = str(request.payload.get("objective_sha256") or "")
                    if not _SHA256.fullmatch(objective_sha):
                        return self._reject_and_record(
                            conn, request, receipt_id, state, control, "OBJECTIVE_SHA256_REQUIRED", now
                        )
                else:
                    objective_sha = str(control["objective_sha256"])
                if request.command == "project.resume" and str(state["status"]) == "CANCELLED":
                    return self._reject_and_record(
                        conn, request, receipt_id, state, control, "PROJECT_TERMINAL", now
                    )

                operator_generation = int(control["operator_generation"]) + 1
                objective_generation = int(control["objective_generation"])
                if request.command in {"project.cancel", "project.supersede"}:
                    objective_generation += 1
                status = {
                    "project.pause": "PAUSED",
                    "project.resume": "RUNNING",
                    "project.cancel": "CANCELLED",
                    "project.supersede": "RUNNING",
                }[request.command]
                phase = "OPERATOR_FENCE" if request.command != "project.resume" else "RUNNING"
                next_version = int(state["state_version"]) + 1
                conn.execute(
                    "UPDATE project_state SET state_version=?,status=?,phase=?,updated_at=? WHERE project_id=? AND state_version=?",
                    (next_version, status, phase, now, request.project_id, int(request.expected_state_version)),
                )
                conn.execute(
                    """
                    UPDATE operator_controls SET operator_state=?,operator_generation=?,
                        objective_generation=?,objective_sha256=?,updated_at=? WHERE project_id=?
                    """,
                    (status, operator_generation, objective_generation, objective_sha, now, request.project_id),
                )
                if request.command in {"project.cancel", "project.supersede"}:
                    # Fence every still-authoritative piece of work before the
                    # receipt is committed. Historical rows remain for audit,
                    # but queued/running work and unverified results cannot be
                    # admitted by a later Master epoch.
                    conn.execute(
                        "UPDATE assignments SET state='FENCED',updated_at=? WHERE project_id=? AND state NOT IN ('FENCED','VERIFIED','ACCEPTED')",
                        (now, request.project_id),
                    )
                    conn.execute(
                        "UPDATE leases SET state='FENCED',released_at=? WHERE project_id=? AND state='ACTIVE'",
                        (now, request.project_id),
                    )
                    conn.execute(
                        "UPDATE task_nodes SET state='FENCED',updated_at=? WHERE project_id=? AND state NOT IN ('VERIFIED','ACCEPTED','FENCED')",
                        (now, request.project_id),
                    )
                    conn.execute(
                        "UPDATE candidate_results SET verification_state='STALE' WHERE project_id=? AND verification_state NOT IN ('STALE','REJECTED')",
                        (request.project_id,),
                    )
                event_id = f"runtime-command-{request.request_id}"
                conn.execute(
                    "INSERT INTO events(event_id,project_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        event_id,
                        request.project_id,
                        "OPERATOR_CONTROL",
                        canonical_json({
                            "request_id": request.request_id,
                            "command": request.command,
                            "operator_generation": operator_generation,
                            "objective_generation": objective_generation,
                            "status": status,
                        }),
                        now,
                    ),
                )
                response = build_response(
                    request,
                    status="OK",
                    daemon_epoch=self.daemon_epoch,
                    state_version=next_version,
                    master_epoch=int(state["master_epoch"]),
                    generation=operator_generation,
                    receipt_id=receipt_id,
                    result={
                        "operator_state": status,
                        "operator_generation": operator_generation,
                        "objective_generation": objective_generation,
                        "objective_sha256": objective_sha,
                    },
                )
                self._insert_receipt(
                    conn,
                    request=request,
                    receipt_id=receipt_id,
                    state=state,
                    actor=self._actor_for_request(request),
                    control={
                        "operator_generation": operator_generation,
                        "objective_generation": objective_generation,
                    },
                    status="OK",
                    reason=status,
                    response=response,
                    created_at=now,
                    output_state_version=next_version,
                )
                return response
        except StoreInvariantError as exc:
            return build_response(
                request,
                status="ERROR",
                daemon_epoch=self.daemon_epoch,
                error={"code": str(exc).split(":", 1)[0], "detail": str(exc)},
            )

    def apply(self, command: str | RuntimeRequest, request: RuntimeRequest | None = None) -> dict[str, Any]:
        """Planned API alias; accepts either a request or (command, request)."""
        target = command if isinstance(command, RuntimeRequest) else request
        if target is None:
            raise ValueError("RUNTIME_REQUEST_REQUIRED")
        if isinstance(command, str) and target.command != command:
            raise ValueError("RUNTIME_COMMAND_MISMATCH")
        return self.execute(target)

    def _reject_and_record(self, conn, request, receipt_id, state, control, reason, now):
        if state is None:
            return build_response(
                request,
                status="REJECTED",
                daemon_epoch=self.daemon_epoch,
                error={"code": reason},
            )
        response = build_response(
            request,
            status="REJECTED",
            daemon_epoch=self.daemon_epoch,
            state_version=int(state["state_version"]) if state is not None else None,
            master_epoch=int(state["master_epoch"]) if state is not None else None,
            generation=int(control["operator_generation"]) if control is not None else None,
            receipt_id=receipt_id,
            error={"code": reason},
        )
        self._insert_receipt(
            conn,
            request=request,
            receipt_id=receipt_id,
            state=state,
            actor=self._actor_for_request(request),
            control=control,
            status="REJECTED",
            reason=reason,
            response=response,
            created_at=now,
            output_state_version=int(state["state_version"]) if state is not None else None,
        )
        return response

    @staticmethod
    def _insert_receipt(
        conn,
        *,
        request: RuntimeRequest,
        receipt_id: str,
        state,
        actor: str,
        control,
        status: str,
        reason: str,
        response: Mapping[str, Any],
        created_at: str,
        output_state_version: int | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO runtime_command_receipts(
                request_id,receipt_id,project_id,command,actor,input_state_version,
                output_state_version,daemon_epoch,master_epoch,operator_generation,
                objective_generation,payload_sha256,status,reason,response_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request.request_id,
                receipt_id,
                request.project_id,
                request.command,
                actor,
                request.expected_state_version,
                output_state_version,
                request.expected_daemon_epoch,
                int(state["master_epoch"]) if state is not None else None,
                int(control["operator_generation"]) if control is not None else None,
                int(control["objective_generation"]) if control is not None else None,
                request.payload_sha256,
                status,
                reason,
                canonical_json(dict(response)),
                created_at,
            ),
        )
