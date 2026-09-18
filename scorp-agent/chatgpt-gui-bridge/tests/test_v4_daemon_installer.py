from __future__ import annotations

import pathlib
import unittest


class V4DaemonInstallerTests(unittest.TestCase):
    def test_installer_is_transactional_and_binds_the_v4_daemon_entrypoint(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        for token in (
            "Export-ScheduledTask",
            "Register-ScheduledTask",
            "Unregister-ScheduledTask",
            "Start-ScheduledTask",
            "MultipleInstances IgnoreNew",
            "StartWhenAvailable",
            "Interactive",
            "v4_release_runtime.py",
            "daemon_epoch",
            "SCORP_V4_DAEMON",
        ):
            self.assertIn(token, text)
        self.assertNotIn(
            "Join-Path $PSScriptRoot 'tools\\v4_daemon_runtime.py'",
            text,
        )

    def test_installer_registers_persistent_active_controller_startup(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        self.assertIn("[Parameter(Mandatory=$true)][string]$DriverStatePath", text)
        self.assertIn("[string]$MasterSessionId = 'master-a-runtime'", text)
        for token in (
            "--active-controller",
            "--driver-state-path",
            "--supervise-master",
            "--master-session-id",
        ):
            self.assertIn(token, text)
    def test_installer_refuses_missing_database_and_has_rollback_boundary(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        self.assertIn("STATE_DATABASE_MISSING", text)
        self.assertIn("$installSucceeded = $false", text)
        self.assertIn("finally", text)


if __name__ == "__main__":
    unittest.main()
