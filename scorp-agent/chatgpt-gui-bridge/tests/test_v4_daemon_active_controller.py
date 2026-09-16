from __future__ import annotations

import types
import unittest

from master_a_dynamic_v4.activation_arbiter import ActivationDecision
from tools import v4_daemon_runtime as runtime


class _Controller:
    def __init__(self):
        self.step_calls = 0

    def step(self, prompt_factory):
        self.step_calls += 1
        prompt_factory(types.SimpleNamespace(task_id="T1"))
        return types.SimpleNamespace(status="IDLE", blockers=(), outcomes=())


class _Gateway:
    def __init__(self, recovery):
        self.recovery = recovery
        self.recover_calls = 0

    def recover(self):
        self.recover_calls += 1
        return list(self.recovery)


def _decision(action: str):
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


class V4DaemonActiveControllerTests(unittest.TestCase):
    def test_active_controller_is_explicit_and_disabled_by_default(self):
        parser = runtime.build_parser()
        args = parser.parse_args(
            [
                "--database-path", "state.sqlite3",
                "--allowed-root", ".",
                "--project-id", "p",
            ]
        )
        self.assertFalse(args.active_controller)
        enabled = parser.parse_args(
            [
                "--database-path", "state.sqlite3",
                "--allowed-root", ".",
                "--project-id", "p",
                "--active-controller",
            ]
        )
        self.assertTrue(enabled.active_controller)

    def test_active_controller_requires_driver_state_path_before_browser_capability(self):
        parser = runtime.build_parser()
        args = parser.parse_args(
            [
                "--database-path", "state.sqlite3",
                "--allowed-root", ".",
                "--project-id", "p",
                "--active-controller",
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "DRIVER_STATE_PATH_REQUIRED_FOR_ACTIVE_CONTROLLER"):
            runtime._validate_active_controller_options(args)

    def test_action_map_routes_ambiguity_to_recovery_and_safe_work_to_one_step(self):
        controller = _Controller()
        gateway = _Gateway([("intent-1", "RESPONSE_CAPTURED")])
        prompts = []
        handlers = runtime._build_persistent_action_handlers(
            controller,
            gateway,
            worker_prompt_factory=lambda claim: prompts.append(claim.task_id) or "prompt",
        )
        self.assertEqual(
            {
                "RECONCILE_AMBIGUOUS",
                "ASSIGN_WORKER",
                "WAKE_MASTER",
                "RESUME_WORKER",
                "RECOVER_STALLED",
            },
            set(handlers),
        )
        recovered = handlers["RECONCILE_AMBIGUOUS"](_decision("RECONCILE_AMBIGUOUS"))
        self.assertEqual("RECOVERED", recovered["status"])
        self.assertEqual(1, gateway.recover_calls)
        self.assertEqual(0, controller.step_calls)
        handlers["ASSIGN_WORKER"](_decision("ASSIGN_WORKER"))
        self.assertEqual(1, controller.step_calls)
        self.assertEqual(["T1"], prompts)

    def test_action_map_never_steps_when_recovery_remains_ambiguous(self):
        controller = _Controller()
        gateway = _Gateway([("intent-1", "BLOCKED_AMBIGUOUS")])
        handlers = runtime._build_persistent_action_handlers(
            controller,
            gateway,
            worker_prompt_factory=lambda claim: "prompt",
        )
        result = handlers["RECONCILE_AMBIGUOUS"](_decision("RECONCILE_AMBIGUOUS"))
        self.assertEqual("WAITING", result["status"])
        self.assertEqual("BROWSER_RECONCILIATION_PENDING", result["reason"])
        self.assertEqual(0, controller.step_calls)


if __name__ == "__main__":
    unittest.main()
