from __future__ import annotations

import types
import unittest

from master_a_dynamic_v4.activation_arbiter import ActivationDecision
from master_a_dynamic_v4.daemon import PersistentControllerActionHandler


class _Controller:
    def __init__(self, status="IDLE"):
        self.status = status
        self.step_calls = 0

    def step(self, prompt_factory):
        self.step_calls += 1
        prompt_factory(types.SimpleNamespace(task_id="T1"))
        return types.SimpleNamespace(status=self.status, blockers=(), outcomes=())


def _decision(action):
    return ActivationDecision(
        decision_id="d1",
        project_id="p",
        actor_id="daemon",
        daemon_epoch=1,
        master_epoch=1,
        action=action,
        reason="test",
        capacity=1,
        input_sha256="a" * 64,
    )


class PersistentControllerActionHandlerTests(unittest.TestCase):
    def test_ambiguous_action_uses_recovery_only_and_never_steps_controller(self):
        controller = _Controller()
        recover_calls = []
        prompts = []
        handler = PersistentControllerActionHandler(
            controller,
            worker_prompt_factory=lambda claim: prompts.append(claim.task_id) or "prompt",
            recover_callback=lambda: recover_calls.append(True) or [("intent-1", "RESPONSE_CAPTURED")],
        )
        result = handler(_decision("RECONCILE_AMBIGUOUS"))
        self.assertEqual("RECOVERED", result["status"])
        self.assertEqual([True], recover_calls)
        self.assertEqual(0, controller.step_calls)
        self.assertEqual([], prompts)

    def test_ambiguous_action_stays_blocked_when_recovery_is_still_uncertain(self):
        controller = _Controller()
        handler = PersistentControllerActionHandler(
            controller,
            worker_prompt_factory=lambda claim: "prompt",
            recover_callback=lambda: [("intent-1", "BLOCKED_AMBIGUOUS")],
        )
        result = handler(_decision("RECONCILE_AMBIGUOUS"))
        self.assertEqual("WAITING", result["status"])
        self.assertEqual("BROWSER_RECONCILIATION_PENDING", result["reason"])
        self.assertEqual(0, controller.step_calls)

    def test_assign_worker_runs_exactly_one_bounded_controller_step(self):
        controller = _Controller(status="IDLE")
        prompts = []
        handler = PersistentControllerActionHandler(
            controller,
            worker_prompt_factory=lambda claim: prompts.append(claim.task_id) or "prompt",
            recover_callback=lambda: self.fail("recover must not run for ASSIGN_WORKER"),
        )
        result = handler(_decision("ASSIGN_WORKER"))
        self.assertEqual("IDLE", result["status"])
        self.assertEqual(1, controller.step_calls)
        self.assertEqual(["T1"], prompts)

    def test_blocked_controller_step_propagates_fail_closed(self):
        controller = _Controller(status="BLOCKED")
        handler = PersistentControllerActionHandler(
            controller,
            worker_prompt_factory=lambda claim: "prompt",
            recover_callback=lambda: [],
        )
        result = handler(_decision("WAKE_MASTER"))
        self.assertEqual("BLOCKED", result["status"])

    def test_reason_master_uses_reasoning_callback_not_controller_step(self):
        controller = _Controller()
        calls = []
        handler = PersistentControllerActionHandler(
            controller,
            worker_prompt_factory=lambda claim: "prompt",
            recover_callback=lambda: [],
            reasoning_callback=lambda: calls.append(True) or {"status": "APPLIED"},
        )
        result = handler(_decision("REASON_MASTER"))
        self.assertEqual("APPLIED", result["status"])
        self.assertEqual([True], calls)
        self.assertEqual(0, controller.step_calls)

    def test_reason_master_without_callback_fails_closed(self):
        controller = _Controller()
        handler = PersistentControllerActionHandler(
            controller,
            worker_prompt_factory=lambda claim: "prompt",
            recover_callback=lambda: [],
        )
        result = handler(_decision("REASON_MASTER"))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("MASTER_REASONING_CALLBACK_REQUIRED", result["reason"])
        self.assertEqual(0, controller.step_calls)

    def test_unsupported_action_is_rejected(self):
        handler = PersistentControllerActionHandler(
            _Controller(),
            worker_prompt_factory=lambda claim: "prompt",
            recover_callback=lambda: [],
        )
        with self.assertRaisesRegex(RuntimeError, "PERSISTENT_CONTROLLER_ACTION_UNSUPPORTED"):
            handler(_decision("EMERGENCY_STOP"))


if __name__ == "__main__":
    unittest.main()
