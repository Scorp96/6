"""Local supervisor seam for the durable Master A session.

    The supervisor is deliberately smaller than the controller. It polls the
    SQLite-backed watchdog and renews a live logical session. After expiry it
    requests a new fencing epoch only when a physical-browser rebind callback is
    supplied; without that callback it leaves the old state untouched and emits
    a durable handoff signal.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Mapping
from typing import Any


@dataclasses.dataclass(frozen=True)
class SupervisorDecision:
    """Auditable result of one supervisor polling pass."""

    status: str
    reason: str
    watchdog: Mapping[str, Any]
    heartbeat: Mapping[str, Any] | None = None
    resume: Mapping[str, Any] | None = None


@dataclasses.dataclass(frozen=True)
class SupervisorLoopResult:
    """Bounded outcome from a local monitor loop."""

    status: str
    stop_reason: str
    decisions: tuple[SupervisorDecision, ...]


class MasterSupervisor:
    """Keep one logical Master A alive without owning browser I/O.

    ``rebind_callback`` belongs to the physical-session adapter. It receives the
    result of ``controller.resume()`` only after a new epoch has been acquired.
    A callback failure ends that unbound epoch when the controller supports
    ``end()`` and is reported as ``BLOCKED``; it is never presented as a
    successful recovery.
    """

    def __init__(
        self,
        controller: Any,
        *,
        rebind_callback: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        if controller is None:
            raise ValueError("MASTER_CONTROLLER_REQUIRED")
        self.controller = controller
        self.rebind_callback = rebind_callback

    def run_once(self) -> SupervisorDecision:
        watchdog = self._mapping(self.controller.watchdog_once(), "WATCHDOG_RESULT_INVALID")
        status = str(watchdog.get("status") or "").strip()

        if status == "MASTER_ACTIVE":
            try:
                heartbeat = self._mapping(self.controller.heartbeat(), "HEARTBEAT_RESULT_INVALID")
            except Exception as exc:  # keep the monitor alive and fail closed
                return SupervisorDecision(
                    status="BLOCKED",
                    reason=f"HEARTBEAT_FAILED:{type(exc).__name__}",
                    watchdog=watchdog,
                )
            return SupervisorDecision(
                status="MASTER_ACTIVE",
                reason="HEARTBEAT_RENEWED",
                watchdog=watchdog,
                heartbeat=heartbeat,
            )

        if status == "RESUME_REQUIRED":
            if self.rebind_callback is None:
                return SupervisorDecision(
                    status="RESUME_REQUIRED",
                    reason="PHYSICAL_REBIND_REQUIRED",
                    watchdog=watchdog,
                )
            try:
                resume = self._mapping(self.controller.resume(), "RESUME_RESULT_INVALID")
            except Exception as exc:
                return SupervisorDecision(
                    status="BLOCKED",
                    reason=f"RESUME_FAILED:{type(exc).__name__}",
                    watchdog=watchdog,
                )
            try:
                self.rebind_callback(resume)
            except Exception as exc:
                cleanup_reason = "PHYSICAL_REBIND_FAILED"
                cleanup_error = ""
                end = getattr(self.controller, "end", None)
                if callable(end):
                    try:
                        end(reason=cleanup_reason)
                    except Exception as cleanup_exc:
                        cleanup_error = f":CLEANUP_FAILED:{type(cleanup_exc).__name__}"
                return SupervisorDecision(
                    status="BLOCKED",
                    reason=f"REBIND_FAILED:{type(exc).__name__}{cleanup_error}",
                    watchdog=watchdog,
                    resume=resume,
                )
            return SupervisorDecision(
                status="MASTER_ACTIVE",
                reason="RESUMED_AND_REBOUND",
                watchdog=watchdog,
                resume=resume,
            )

        if status == "TERMINAL":
            return SupervisorDecision(
                status="TERMINAL",
                reason="MASTER_TERMINAL",
                watchdog=watchdog,
            )

        return SupervisorDecision(
            status="BLOCKED",
            reason="WATCHDOG_STATUS_UNKNOWN",
            watchdog=watchdog,
        )

    def run_loop(
        self,
        *,
        interval_seconds: float = 30.0,
        max_iterations: int | None = None,
        stop_event: Any | None = None,
        sleep: Callable[[float], Any] = time.sleep,
        on_decision: Callable[[SupervisorDecision], Any] | None = None,
    ) -> SupervisorLoopResult:
        """Poll until a stop condition, a caller stop event, or a bound.

        This is intentionally a host-process seam. It never opens a browser or
        catches a blocker and keeps retrying it. A finite ``max_iterations`` is
        useful for Task Scheduler probes; a long-lived monitor can provide a
        stop event and omit the bound.
        """

        interval = float(interval_seconds)
        if interval < 0:
            raise ValueError("SUPERVISOR_INTERVAL_INVALID")
        if max_iterations is not None and int(max_iterations) <= 0:
            raise ValueError("SUPERVISOR_ITERATION_BOUND_INVALID")
        decisions: list[SupervisorDecision] = []
        while True:
            decision = self.run_once()
            decisions.append(decision)
            if on_decision is not None:
                on_decision(decision)
            if decision.status in {"TERMINAL", "BLOCKED", "RESUME_REQUIRED"}:
                return SupervisorLoopResult(
                    status=decision.status,
                    stop_reason=decision.reason,
                    decisions=tuple(decisions),
                )
            if max_iterations is not None and len(decisions) >= int(max_iterations):
                return SupervisorLoopResult(
                    status=decision.status,
                    stop_reason="MAX_ITERATIONS",
                    decisions=tuple(decisions),
                )
            if stop_event is not None and bool(stop_event.is_set()):
                return SupervisorLoopResult(
                    status=decision.status,
                    stop_reason="STOP_EVENT",
                    decisions=tuple(decisions),
                )
            sleep(interval)

    @staticmethod
    def _mapping(value: Any, error: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(error)
        return value
