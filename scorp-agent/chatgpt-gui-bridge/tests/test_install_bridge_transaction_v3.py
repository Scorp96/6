import pathlib
import unittest


class InstallBridgeTransactionV3Tests(unittest.TestCase):
    def setUp(self):
        self.path = pathlib.Path(__file__).resolve().parents[1] / "install-bridge.ps1"
        self.text = self.path.read_text(encoding="utf-8")

    def test_backs_up_both_scheduled_tasks_before_mutation(self):
        self.assertIn("$existingWatchdog = Get-ScheduledTask -TaskName $WatchdogTaskName", self.text)
        self.assertIn("Export-ScheduledTask -TaskName $TaskName", self.text)
        self.assertIn("Export-ScheduledTask -TaskName $WatchdogTaskName", self.text)
        self.assertIn("$mainTaskBackupXml", self.text)
        self.assertIn("$watchdogTaskBackupXml", self.text)

    def test_stops_watchdog_before_main_task_to_close_restart_race(self):
        watchdog_stop = self.text.index("Stop-ScheduledTask -TaskName $WatchdogTaskName")
        main_stop = self.text.index("Stop-ScheduledTask -TaskName $TaskName")
        self.assertLess(watchdog_stop, main_stop)

    def test_install_is_wrapped_in_transactional_rollback(self):
        self.assertIn("$installSucceeded = $false", self.text)
        self.assertIn("try {", self.text)
        self.assertIn("catch {", self.text)
        self.assertIn("BRIDGE_INSTALL_ROLLBACK_FAILED", self.text)
        self.assertIn("Register-ScheduledTask -TaskName $TaskName -Xml $mainTaskBackupXml -Force", self.text)
        self.assertIn("Register-ScheduledTask -TaskName $WatchdogTaskName -Xml $watchdogTaskBackupXml -Force", self.text)

    def test_rollback_restores_prior_presence_and_running_state(self):
        self.assertIn("$mainTaskExisted", self.text)
        self.assertIn("$watchdogTaskExisted", self.text)
        self.assertIn("$mainTaskWasRunning", self.text)
        self.assertIn("$watchdogTaskWasRunning", self.text)
        self.assertIn("Unregister-ScheduledTask -TaskName $TaskName", self.text)
        self.assertIn("Unregister-ScheduledTask -TaskName $WatchdogTaskName", self.text)
        self.assertIn("Start-ScheduledTask -TaskName $TaskName", self.text)
        self.assertIn("Start-ScheduledTask -TaskName $WatchdogTaskName", self.text)

    def test_rollback_restores_install_directory_backup(self):
        self.assertIn("$installDirExisted", self.text)
        self.assertIn("$backupDir", self.text)
        self.assertIn("Remove-Item -LiteralPath $InstallDir -Recurse -Force", self.text)
        self.assertIn("Get-ChildItem -LiteralPath $backupDir -Force | Copy-Item -Destination $InstallDir -Recurse -Force", self.text)

    def test_manifest_deploys_installer_itself(self):
        self.assertIn("'install-bridge.ps1'", self.text)

    def test_success_path_still_uses_force_registration_and_single_task_names(self):
        self.assertIn("Register-ScheduledTask -TaskName $TaskName", self.text)
        self.assertIn("Register-ScheduledTask -TaskName $WatchdogTaskName", self.text)
        self.assertIn("-Force | Out-Null", self.text)
        self.assertIn("BRIDGE_INSTALL_PASS", self.text)

    def test_background_defaults_use_windowless_python_and_hidden_watchdog(self):
        for token in (
            "Resolve-WindowlessPython",
            "pythonw.exe",
            "-Execute $windowlessPython",
            "'-WindowStyle','Hidden'",
            "'-NonInteractive'",
            "[string]$actualAction.Execute -ne $windowlessPython",
        ):
            self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
