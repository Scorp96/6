from __future__ import annotations

import unittest


class MasterSupervisorTests(unittest.TestCase):
    def test_physical_health_failure_does_not_renew_logical_lease(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "MASTER_ACTIVE"}])
        supervisor = MasterSupervisor(
            controller,
            physical_health_probe=lambda: {"status": "PHYSICAL_UNAVAILABLE", "reason": "TAB_LOST"},
        )
        decision = supervisor.run_once()
        self.assertEqual("RESUME_REQUIRED", decision.status)
        self.assertEqual("PHYSICAL_HEALTH_FAILED:TAB_LOST", decision.reason)
        self.assertEqual(0, controller.heartbeats)

    def test_physical_health_failure_rebinds_before_any_heartbeat(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        rebinds = []
        controller = _Controller([{"status": "MASTER_ACTIVE"}])
        supervisor = MasterSupervisor(
            controller,
            physical_health_probe=lambda: {"status": "PHYSICAL_UNAVAILABLE", "reason": "RELAY_DEAD"},
            rebind_callback=rebinds.append,
        )
        decision = supervisor.run_once()
        self.assertEqual("MASTER_ACTIVE", decision.status)
        self.assertEqual("RESUMED_AND_REBOUND", decision.reason)
        self.assertEqual(0, controller.heartbeats)
        self.assertEqual(1, controller.resumes)
        self.assertEqual([{"master_epoch": 2}], rebinds)

    def test_auth_or_rate_limit_block_does_not_create_new_epoch(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "MASTER_ACTIVE"}])
        supervisor = MasterSupervisor(
            controller,
            physical_health_probe=lambda: {
                "status": "PHYSICAL_UNAVAILABLE",
                "reason": "AUTH_BLOCKED:BROWSER_RATE_LIMITED",
            },
            rebind_callback=lambda _resume: (_ for _ in ()).throw(AssertionError("must not rebind")),
        )
        decision = supervisor.run_once()
        self.assertEqual("BLOCKED", decision.status)
        self.assertEqual(
            "PHYSICAL_HEALTH_BLOCKED:AUTH_BLOCKED:BROWSER_RATE_LIMITED",
            decision.reason,
        )
        self.assertEqual(0, controller.resumes)
        self.assertEqual(0, controller.heartbeats)

    def test_active_session_renews_without_rebinding(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "MASTER_ACTIVE"}])
        supervisor = MasterSupervisor(controller)
        decision = supervisor.run_once()
        self.assertEqual("MASTER_ACTIVE", decision.status)
        self.assertEqual(1, controller.heartbeats)
        self.assertEqual(0, controller.resumes)

    def test_expired_session_resumes_then_calls_physical_rebind(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        rebinds = []
        controller = _Controller([{"status": "RESUME_REQUIRED"}])
        supervisor = MasterSupervisor(controller, rebind_callback=rebinds.append)
        decision = supervisor.run_once()
        self.assertEqual("MASTER_ACTIVE", decision.status)
        self.assertEqual(1, controller.resumes)
        self.assertEqual([{"master_epoch": 2}], rebinds)

    def test_expired_session_without_rebind_callback_does_not_advance_epoch(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "RESUME_REQUIRED"}])
        decision = MasterSupervisor(controller).run_once()
        self.assertEqual("RESUME_REQUIRED", decision.status)
        self.assertEqual("PHYSICAL_REBIND_REQUIRED", decision.reason)
        self.assertEqual(0, controller.resumes)

    def test_rebind_failure_remains_blocked(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        def fail(_resume):
            raise RuntimeError("browser unavailable")

        controller = _Controller([{"status": "RESUME_REQUIRED"}])
        decision = MasterSupervisor(controller, rebind_callback=fail).run_once()
        self.assertEqual("BLOCKED", decision.status)
        self.assertIn("REBIND_FAILED", decision.reason)
        self.assertEqual(1, controller.ends)

    def test_terminal_session_is_not_restarted(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "TERMINAL"}])
        decision = MasterSupervisor(controller).run_once()
        self.assertEqual("TERMINAL", decision.status)
        self.assertEqual(0, controller.heartbeats)
        self.assertEqual(0, controller.resumes)

    def test_loop_stops_on_terminal_and_reports_decisions(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "MASTER_ACTIVE"}, {"status": "TERMINAL"}])
        sleeps = []
        result = MasterSupervisor(controller).run_loop(
            interval_seconds=0.25,
            sleep=sleeps.append,
        )
        self.assertEqual("TERMINAL", result.status)
        self.assertEqual("MASTER_TERMINAL", result.stop_reason)
        self.assertEqual(2, len(result.decisions))
        self.assertEqual([0.25], sleeps)

    def test_loop_honors_iteration_bound(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "MASTER_ACTIVE"}, {"status": "MASTER_ACTIVE"}])
        result = MasterSupervisor(controller).run_loop(
            interval_seconds=0,
            max_iterations=2,
            sleep=lambda _seconds: None,
        )
        self.assertEqual("MASTER_ACTIVE", result.status)
        self.assertEqual("MAX_ITERATIONS", result.stop_reason)
        self.assertEqual(2, len(result.decisions))

    def test_loop_rejects_invalid_bounds(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "MASTER_ACTIVE"}])
        supervisor = MasterSupervisor(controller)
        with self.assertRaisesRegex(ValueError, "SUPERVISOR_INTERVAL_INVALID"):
            supervisor.run_loop(interval_seconds=-1, max_iterations=1)
        with self.assertRaisesRegex(ValueError, "SUPERVISOR_ITERATION_BOUND_INVALID"):
            supervisor.run_loop(interval_seconds=0, max_iterations=0)


class _Controller:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.heartbeats = 0
        self.resumes = 0
        self.ends = 0

    def watchdog_once(self):
        return self.decisions.pop(0)

    def heartbeat(self):
        self.heartbeats += 1
        return {"state": "ACTIVE"}

    def resume(self):
        self.resumes += 1
        return {"master_epoch": 2}

    def end(self, *, reason):
        self.ends += 1
        return {"state": "ENDED", "reason": reason}


if __name__ == "__main__":
    unittest.main()
