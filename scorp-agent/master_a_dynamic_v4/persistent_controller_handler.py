"""Fenced action adapter from the local daemon to the existing Master A controller."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .activation_arbiter import ActivationDecision


class PersistentControllerActionHandler:
    """Route safe daemon decisions through existing controller/recovery seams.

    Ambiguous external browser effects are never handed to ``controller.step``.
    They first pass through the injected recovery callback. Scheduling actions
    execute at most one bounded controller step, preserving the controller's
    durable intent fence and two-slot capacity.
    """

    _STEP_ACTIONS = frozenset(
        {
            "ASSIGN_WORKER",
            "WAKE_MASTER",
            "RESUME_WORKER",
            "RECOVER_STALLED",
        }
    )
    _AMBIGUOUS_STATES = frozenset(
        {
            "MAY_HAVE_SUBMITTED",
            "BLOCKED_AMBIGUOUS",
            "CONFIRMED_SUBMITTED",
        }
    )

    def __init__(
        self,
        controller: Any,
        *,
        worker_prompt_factory: Callable[[Any], str],
        recover_callback: Callable[[], Any],
    ) -> None:
        if controller is None or not callable(getattr(controller, "step", None)):
            raise ValueError("PERSISTENT_CONTROLLER_REQUIRED")
        if not callable(worker_prompt_factory):
            raise ValueError("WORKER_PROMPT_FACTORY_REQUIRED")
        if not callable(recover_callback):
            raise ValueError("BROWSER_RECOVERY_CALLBACK_REQUIRED")
        self.controller = controller
        self.worker_prompt_factory = worker_prompt_factory
        self.recover_callback = recover_callback

    def __call__(self, decision: ActivationDecision) -> dict[str, Any]:
        action = str(getattr(decision, "action", "") or "").strip()
        if action == "RECONCILE_AMBIGUOUS":
            recovered = self.recover_callback()
            items = list(recovered or ())
            unresolved = []
            for item in items:
                state = ""
                if isinstance(item, Mapping):
                    state = str(item.get("state") or item.get("status") or "")
                elif isinstance(item, (tuple, list)) and len(item) >= 2:
                    state = str(item[1] or "")
                if state in self._AMBIGUOUS_STATES:
                    unresolved.append(item)
            if unresolved:
                return {
                    "status": "BLOCKED",
                    "reason": "BROWSER_RECONCILIATION_REQUIRED",
                    "recovery": items,
                }
            return {"status": "RECOVERED", "recovery": items}

        if action in self._STEP_ACTIONS:
            step = self.controller.step(self.worker_prompt_factory)
            if isinstance(step, Mapping):
                status = str(step.get("status") or "").strip().upper()
                blockers = step.get("blockers", ())
                outcomes = step.get("outcomes", ())
            else:
                status = str(getattr(step, "status", "") or "").strip().upper()
                blockers = getattr(step, "blockers", ())
                outcomes = getattr(step, "outcomes", ())
            if not status:
                return {
                    "status": "BLOCKED",
                    "reason": "CONTROLLER_STEP_STATUS_MISSING",
                }
            result = {
                "status": status,
                "blockers": list(blockers or ()),
                "outcomes": list(outcomes or ()),
            }
            if status == "BLOCKED":
                result["reason"] = (
                    ";".join(str(value) for value in (blockers or ()))
                    or "CONTROLLER_STEP_BLOCKED"
                )
            return result

        raise RuntimeError(
            "PERSISTENT_CONTROLLER_ACTION_UNSUPPORTED:" + (action or "UNKNOWN")
        )
