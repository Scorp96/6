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
            "v4_daemon_runtime.py",
            "daemon_epoch",
            "SCORP_V4_DAEMON",
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
