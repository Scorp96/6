"""Small, bounded local machine-dog loop for the V4 transaction core."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time
from collections.abc import Callable, Mapping
from typing import Any

from .activation_arbiter import ActivationArbiter, ActivationDecision, ArbiterSnapshot
from .state_store import StateStore

UTC = dt.timezone.utc


def _now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DaemonInvariantError(RuntimeError):
    pass


class MasterSupervisorActionHandler:
    """Adapt the existing MasterSupervisor to a daemon action handler.

    The handler only accepts a positive supervisor result. Physical browser
    rebind remains the supervisor's injected callback; this adapter never
    opens a browser or retries a blocked operation.
    """

    def __init__(self, supervisor: Any) -> None:
        if supervisor is None or not callable(getattr(supervisor, "run_once", None)):
            raise ValueError("MASTER_SUPERVISOR_REQUIRED")
        self.supervisor = supervisor

    def __call__(self, _decision: ActivationDecision) -> Any:
        result = self.supervisor.run_once()
        status = str(getattr(result, "status", result.get("status") if isinstance(result, Mapping) else ""))
        reason = str(getattr(result, "reason", result.get("reason") if isinstance(result, Mapping) else ""))
        if status in {"MASTER_ACTIVE", "TERMINAL"}:
            return result
        raise RuntimeError(f"MASTER_SUPERVISOR_{status or 'UNKNOWN'}:{reason or 'NO_REASON'}")


class LocalDaemon:
    """Run one deterministic decision pass without owning browser I/O.

    ``snapshot_provider`` is the only runtime-specific seam.  Action handlers
    must call fenced adapters; this class never retries an ambiguous browser
    action and never invents a new assignment itself.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        project_id: str,
        daemon_epoch: int,
        snapshot_provider: Callable[[], ArbiterSnapshot | Mapping[str, object]],
        action_handlers: Mapping[str, Callable[[ActivationDecision], Any]] | None = None,
        lease_heartbeat: Callable[[], Any] | None = None,
        health_path: str | pathlib.Path,
        actor_id: str = "scorp-daemon",
    ) -> None:
        if store is None or not str(project_id).strip():
            raise ValueError("DAEMON_STORE_AND_PROJECT_REQUIRED")
        if int(daemon_epoch) < 0:
            raise ValueError("DAEMON_EPOCH_INVALID")
        if not callable(snapshot_provider):
            raise ValueError("DAEMON_SNAPSHOT_PROVIDER_REQUIRED")
        self.store = store
        self.project_id = str(project_id).strip()
        self.daemon_epoch = int(daemon_epoch)
        self.snapshot_provider = snapshot_provider
        self.action_handlers = dict(action_handlers or {})
        self.lease_heartbeat = lease_heartbeat
        self.health_path = pathlib.Path(health_path).resolve()
        self.health_path.parent.mkdir(parents=True, exist_ok=True)
        self.arbiter = ActivationArbiter(actor_id=actor_id)
        self._last_progress_at: str | None = None
        self._last_observation: dict[str, Any] = {}
        self._last_run_status: str | None = None

    def run_once(self) -> ActivationDecision:
        snapshot_raw = self.snapshot_provider()
        snapshot = snapshot_raw if isinstance(snapshot_raw, ArbiterSnapshot) else ArbiterSnapshot(**dict(snapshot_raw))
        if snapshot.project_id != self.project_id or snapshot.daemon_epoch != self.daemon_epoch:
            self._write_health(
                status="BLOCKED",
                snapshot=snapshot,
                error="DAEMON_EPOCH_OR_PROJECT_MISMATCH",
            )
            raise DaemonInvariantError("DAEMON_EPOCH_OR_PROJECT_MISMATCH")
        if snapshot.progress_state not in {"IDLE", "ACTIVE_GENERATING", "ACTIVE_NO_VISIBLE_PROGRESS", "STALLED_SUSPECTED", "STALLED_CONFIRMED"}:
            self._write_health(status="BLOCKED", snapshot=snapshot, error="PROGRESS_STATE_INVALID")
            raise DaemonInvariantError("PROGRESS_STATE_INVALID")
        if self.lease_heartbeat is not None:
            try:
                self.lease_heartbeat()
            except Exception:
                self._write_health(status="BLOCKED", snapshot=snapshot, error="DAEMON_LEASE_HEARTBEAT_FAILED")
                raise
        try:
            observation = self.store.record_runtime_observation(
                self.project_id,
                progress_state=snapshot.progress_state,
                browser_semantic_state=getattr(snapshot, "browser_semantic_state", "UNKNOWN"),
                auth_host_blocker=getattr(snapshot, "auth_host_blocker", None),
                observed_at=_now(),
                content_changed=bool(getattr(snapshot, "content_changed", False)),
                progress_made=bool(getattr(snapshot, "progress_made", False)),
                browser_succeeded=bool(getattr(snapshot, "browser_succeeded", False)),
                browser_error=bool(getattr(snapshot, "browser_error", False)),
            )
            self._last_observation = observation
            self._last_progress_at = observation.get("last_progress_at")
        except Exception as exc:
            self._write_health(status="BLOCKED", snapshot=snapshot, error=f"RUNTIME_OBSERVATION_FAILED:{type(exc).__name__}")
            raise

        decision = self.arbiter.decide(snapshot)
        # A daemon lease may expire while the read-only observation and
        # deterministic decision are being computed.  Re-check immediately
        # before journaling or invoking any action so a stale daemon cannot
        # authorise a new side effect.
        if self.lease_heartbeat is not None:
            try:
                self.lease_heartbeat()
            except Exception:
                self._write_health(
                    status="BLOCKED",
                    snapshot=snapshot,
                    decision=decision,
                    error="DAEMON_LEASE_FENCE_BEFORE_ACTION",
                )
                raise
        self.store.record_activation_decision(decision.as_dict())
        status = "HEALTHY"
        error: str | None = None
        handler = self.action_handlers.get(decision.action)
        if decision.action in {"TERMINAL"}:
            status = "TERMINAL"
        elif decision.action not in {"TERMINAL", "HEARTBEAT_IDLE"} and handler is None:
            status = "BLOCKED"
            error = f"ACTION_HANDLER_REQUIRED:{decision.action}"
        elif handler is not None:
            try:
                result = handler(decision)
                if isinstance(result, Mapping):
                    result_status = str(result.get("status") or "").strip().upper()
                    if result_status in {"BLOCKED", "FAIL", "FAILED", "ERROR", "REJECTED"}:
                        status = "BLOCKED"
                        reason = str(result.get("reason") or result_status).strip()
                        error = f"ACTION_BLOCKED:{reason}"
            except Exception as exc:  # fail closed but leave the decision durable
                status = "BLOCKED"
                error = f"ACTION_FAILED:{type(exc).__name__}"
        self._write_health(status=status, snapshot=snapshot, decision=decision, error=error)
        self._last_run_status = status
        return decision

    def run_loop(
        self,
        *,
        interval_seconds: float = 5.0,
        max_iterations: int | None = None,
        stop_event: Any | None = None,
        sleep: Callable[[float], Any] = time.sleep,
    ) -> list[ActivationDecision]:
        if float(interval_seconds) < 0:
            raise ValueError("DAEMON_INTERVAL_INVALID")
        if max_iterations is not None and int(max_iterations) <= 0:
            raise ValueError("DAEMON_ITERATION_BOUND_INVALID")
        decisions: list[ActivationDecision] = []
        while max_iterations is None or len(decisions) < int(max_iterations):
            if stop_event is not None and bool(stop_event.is_set()):
                break
            decisions.append(self.run_once())
            if self._last_run_status in {"BLOCKED", "TERMINAL"}:
                break
            if stop_event is not None and bool(stop_event.is_set()):
                break
            if max_iterations is None or len(decisions) < int(max_iterations):
                sleep(float(interval_seconds))
        return decisions

    def _write_health(
        self,
        *,
        status: str,
        snapshot: ArbiterSnapshot,
        decision: ActivationDecision | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "protocol_version": "scorp.v4.daemon-health/1",
            "status": status,
            "project_id": self.project_id,
            "daemon_epoch": self.daemon_epoch,
            "heartbeat_at": _now(),
            "liveness": {
                "state": snapshot.progress_state,
                "last_progress_at": self._last_progress_at,
                "last_observed_at": self._last_observation.get("last_observed_at"),
                "last_heartbeat_at": self._last_observation.get("last_heartbeat_at"),
                "last_content_change_at": self._last_observation.get("last_content_change_at"),
                "last_browser_success_at": self._last_observation.get("last_browser_success_at"),
                "last_browser_error_at": self._last_observation.get("last_browser_error_at"),
                "observed_at": _now(),
            },
            "error": error,
        }
        if decision is not None:
            payload["last_decision"] = decision.as_dict()
        temporary = self.health_path.with_name(self.health_path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.health_path)
