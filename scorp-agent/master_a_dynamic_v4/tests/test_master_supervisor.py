from __future__ import annotations

import unittest


class MasterSupervisorTests(unittest.TestCase):
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
        self.assertEqual("RESUME_REQUIRED", decision.status)
        self.assertEqual(1, controller.resumes)
        self.assertEqual([{"master_epoch": 2}], rebinds)

    def test_rebind_failure_remains_blocked(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        def fail(_resume):
            raise RuntimeError("browser unavailable")

        controller = _Controller([{"status": "RESUME_REQUIRED"}])
        decision = MasterSupervisor(controller, rebind_callback=fail).run_once()
        self.assertEqual("BLOCKED", decision.status)
        self.assertIn("REBIND_FAILED", decision.reason)

    def test_terminal_session_is_not_restarted(self):
        from master_a_dynamic_v4.master_supervisor import MasterSupervisor

        controller = _Controller([{"status": "TERMINAL"}])
        decision = MasterSupervisor(controller).run_once()
        self.assertEqual("TERMINAL", decision.status)
        self.assertEqual(0, controller.heartbeats)
        self.assertEqual(0, controller.resumes)


class _Controller:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.heartbeats = 0
        self.resumes = 0

    def watchdog_once(self):
        return self.decisions.pop(0)

    def heartbeat(self):
        self.heartbeats += 1
        return {"state": "ACTIVE"}

    def resume(self):
        self.resumes += 1
        return {"master_epoch": 2}


if __name__ == "__main__":
    unittest.main()
