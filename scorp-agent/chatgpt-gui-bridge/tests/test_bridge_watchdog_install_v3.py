import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BridgeWatchdogInstallV3Tests(unittest.TestCase):
    def test_watchdog_script_exists_and_is_fail_closed(self):
        path = ROOT / "bridge-watchdog.ps1"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("ScorpChatGptGuiBridge", text)
        self.assertIn("WATCHDOG_RESTARTED", text)
        self.assertIn("WATCHDOG_HEALTHY", text)
        self.assertIn("WATCHDOG_ORPHAN_BLOCKED", text)
        self.assertIn("WATCHDOG_STALE_HEALTH", text)
        self.assertIn("heartbeat_at", text)
        self.assertIn("protocol_version", text)
        self.assertIn("bridge_worker.py", text)
        self.assertIn("Start-ScheduledTask", text)
        self.assertNotIn("Stop-Process", text)
        self.assertNotIn("taskkill", text.lower())
        self.assertNotIn("$parent.CommandLine", text)

    def test_installer_deploys_and_registers_watchdog_every_minute(self):
        text = (ROOT / "install-bridge.ps1").read_text(encoding="utf-8")
        self.assertIn("bridge-watchdog.ps1", text)
        self.assertIn("ScorpChatGptGuiBridgeWatchdog", text)
        self.assertIn("New-ScheduledTaskTrigger", text)
        self.assertIn("New-TimeSpan -Minutes 1", text)
        self.assertIn("MultipleInstances IgnoreNew", text)


if __name__ == "__main__":
    unittest.main()
