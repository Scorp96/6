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
            "watch-v4-daemon.ps1",
            "V4_WATCHDOG_SCRIPT_MISSING",
            "watchdogTaskName",
            "RepetitionInterval",
            "Register-ScheduledTask -TaskName $watchdogTaskName",
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
    def test_installer_registers_independent_crash_watchdog_and_rolls_it_back(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        for token in (
            '$watchdogTaskName = "$resolvedTaskName-Watchdog"',
            "New-ScheduledTaskTrigger -Once",
            "RepetitionInterval (New-TimeSpan -Minutes 1)",
            "'-PythonExecutable'",
            "'-DatabasePath'",
            "'-MaxHealthAgeSeconds'",
            "'-StartupGraceSeconds'",
            "Start-ScheduledTask -TaskName $watchdogTaskName",
            "Unregister-ScheduledTask -TaskName $watchdogTaskName",
            "$priorWatchdogXml",
        ):
            self.assertIn(token, text)

    def test_watchdog_launcher_owns_powershell_host_flags(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        self.assertIn("$watchdogArguments = @(\n        '-MainTaskName'", text)
        for duplicate in (
            "'-NoProfile'",
            "'-NonInteractive'",
            "'-WindowStyle', 'Hidden'",
            "'-ExecutionPolicy', 'Bypass'",
            "'-File', ('\"{0}\"' -f $watchdogScript)",
        ):
            self.assertNotIn(duplicate, text)

    def test_watchdog_uses_a_non_console_launcher(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        self.assertIn("run_hidden_powershell.py", text)
        self.assertIn("$watchdogLauncher", text)
        self.assertIn("-Execute $windowlessPython", text)
        self.assertNotIn("$watchdogAction = New-ScheduledTaskAction -Execute 'powershell.exe'", text)

    def test_hidden_powershell_launcher_suppresses_console_creation(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "run_hidden_powershell.py"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        for token in (
            "CREATE_NO_WINDOW",
            "STARTF_USESHOWWINDOW",
            "SW_HIDE",
            "-WindowStyle",
            "-NonInteractive",
            "subprocess.run",
        ):
            self.assertIn(token, text)

    def test_main_task_uses_windowless_python_and_watchdog_tracks_that_identity(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        self.assertIn("Resolve-WindowlessPython", text)
        self.assertIn("pythonw.exe", text)
        self.assertIn("-Execute $windowlessPython", text)
        self.assertIn("'-PythonExecutable', ('\"{0}\"' -f $windowlessPython)", text)
        self.assertIn("$registered.Actions[0].Execute -ne $windowlessPython", text)

    def test_live_migration_backups_and_rebinds_existing_tasks_without_changing_arguments(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "migrate-v4-daemon-windowless.ps1"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        for token in (
            "Export-ScheduledTask",
            "Set-ScheduledTask",
            "pythonw.exe",
            "-PythonExecutable",
            "BackupDirectory",
            "AllowWatchdogWithoutPythonBinding",
            "EnsureWatchdogHidden",
            "Stop-ScheduledTask",
            "Start-ScheduledTask",
        ):
            self.assertIn(token, text)

    def test_watchdog_only_demand_starts_absent_runtime_and_never_mutates_sqlite(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "watch-v4-daemon.ps1"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        for token in (
            "Get-ScheduledTask",
            "Get-CimInstance Win32_Process",
            "Start-ScheduledTask -TaskName $MainTaskName",
            "[Parameter(Mandatory=$true)][string]$PythonExecutable",
            "[Parameter(Mandatory=$true)][string]$DatabasePath",
            "$exactPython",
            "'--database-path'",
            "HEALTH_STALE",
            "HEALTH_PROCESS_IDENTITY_MISMATCH",
            "StartupGraceSeconds",
            "RECOVERY_START_REQUESTED",
            "HEALTHY_RUNNING",
        ):
            self.assertIn(token, text)
        self.assertNotIn("sqlite3", text.lower())
        self.assertNotIn("taskkill", text.lower())
        self.assertNotIn("Stop-Process", text)

    def test_watchdog_parses_utc_heartbeat_before_powershell_date_coercion(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "watch-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        for token in (
            "$healthRaw",
            '"heartbeat_at"',
            "DateTimeStyles",
            "InvariantCulture",
            "AdjustToUniversal",
        ):
            self.assertIn(token, text)
        self.assertNotIn(
            "[DateTimeOffset]::Parse([string]$health.heartbeat_at)",
            text,
        )

    def test_installer_refuses_missing_database_and_has_rollback_boundary(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = path.read_text(encoding="utf-8")
        self.assertIn("STATE_DATABASE_MISSING", text)
        self.assertIn("$installSucceeded = $false", text)
        self.assertIn("finally", text)


if __name__ == "__main__":
    unittest.main()
