"""Local supervisor seam for the durable Master A session.

The supervisor is deliberately smaller than the controller.  It polls the
SQLite-backed watchdog, renews a live logical session, and requests a new
fencing epoch after expiry.  Rebinding a physical browser is injected by the
host process so this module cannot claim that a browser was recovered when no
browser operation actually happened.
"""

from __future__ import annotations

import dataclasses
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


class MasterSupervisor:
    """Keep one logical Master A alive without owning browser I/O.

    ``rebind_callback`` belongs to the physical-session adapter.  It receives
    the result of ``controller.resume()`` only after a new epoch has been
    acquired.  A callback failure is reported as ``BLOCKED`` and is never
    presented as a successful recovery.
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
            try:
                resume = self._mapping(self.controller.resume(), "RESUME_RESULT_INVALID")
            except Exception as exc:
                return SupervisorDecision(
                    status="BLOCKED",
                    reason=f"RESUME_FAILED:{type(exc).__name__}",
                    watchdog=watchdog,
                )
            if self.rebind_callback is None:
                return SupervisorDecision(
                    status="RESUME_REQUIRED",
                    reason="PHYSICAL_REBIND_REQUIRED",
                    watchdog=watchdog,
                    resume=resume,
                )
            try:
                self.rebind_callback(resume)
            except Exception as exc:
                return SupervisorDecision(
                    status="BLOCKED",
                    reason=f"REBIND_FAILED:{type(exc).__name__}",
                    watchdog=watchdog,
                    resume=resume,
                )
            return SupervisorDecision(
                status="RESUME_REQUIRED",
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

    @staticmethod
    def _mapping(value: Any, error: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(error)
        return value

