import unittest
import json
import os
import subprocess
import tempfile
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
        self.assertIn("$workers.Count -gt 0 -and $roots.Count -eq 0", text)
        self.assertIn("WATCHDOG_ORPHAN_NO_SCHEDULER_ROOT", text)
        self.assertIn("WATCHDOG_STALE_HEALTH", text)
        self.assertIn("heartbeat_at", text)
        self.assertIn("protocol_version", text)
        self.assertIn("bridge_worker.py", text)
        self.assertIn("Start-ScheduledTask", text)
        self.assertNotIn("Stop-Process", text)
        self.assertNotIn("taskkill", text.lower())
        self.assertNotIn("$parent.CommandLine", text)

    @unittest.skipUnless(os.name == "nt", "Windows watchdog dry-run only")
    def test_rotation_conflict_never_restarts_or_queries_real_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            health = root / "health.json"
            health.write_text(
                json.dumps({
                    "protocol_version": "scorp.gui-bridge/health-v1",
                    "status": "ERROR",
                    "error": "ValueError: MASTER_CONVERSATION_ROTATION_REQUIRED; consecutive_cycle=3",
                }),
                encoding="utf-8",
            )
            # The target task intentionally does not exist. The protected
            # path must return before Get-ScheduledTask/Start-ScheduledTask.
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass",
                    "-File", str(ROOT / "bridge-watchdog.ps1"),
                    "-StateDir", str(root),
                    "-TargetTaskName", "SCORP_Test_Nonexistent_Rotation_Gate",
                ],
                text=True, capture_output=True, timeout=20,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("WATCHDOG_BLOCKED_ROTATION", result.stdout)
            generated = json.loads((root / "watchdog-health.json").read_text(encoding="utf-8"))
            self.assertEqual("WATCHDOG_BLOCKED_ROTATION", generated["status"])

    def test_installer_deploys_and_registers_watchdog_every_minute(self):
        text = (ROOT / "install-bridge.ps1").read_text(encoding="utf-8")
        self.assertIn("bridge-watchdog.ps1", text)
        self.assertIn("ScorpChatGptGuiBridgeWatchdog", text)
        self.assertIn("New-ScheduledTaskTrigger", text)
        self.assertIn("New-TimeSpan -Minutes 1", text)
        self.assertIn("MultipleInstances IgnoreNew", text)


if __name__ == "__main__":
    unittest.main()
