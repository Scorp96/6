from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import tempfile
import unittest


class V4DaemonWatchdogBehaviorTests(unittest.TestCase):
    def _run(self, *, heartbeat_age_seconds: int, last_run_age_seconds: int):
        script = pathlib.Path(__file__).resolve().parents[1] / "watch-v4-daemon.ps1"
        now = dt.datetime.now(dt.timezone.utc)
        health = {
            "protocol_version": "scorp.v4.daemon-health/1",
            "status": "HEALTHY",
            "project_id": "p",
            "daemon_epoch": 7,
            "actor_id": "scorp-daemon-test",
            "process_id": 2222,
            "heartbeat_at": (now - dt.timedelta(seconds=heartbeat_age_seconds)).isoformat().replace("+00:00", "Z"),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            health_path = root / "health.json"
            health_path.write_text(json.dumps(health), encoding="utf-8")
            release = r"C:\release\v4_release_runtime.py"
            python = r"C:\runtime\python.exe"
            database = r"C:\state\state.sqlite3"
            last_run = (dt.datetime.now() - dt.timedelta(seconds=last_run_age_seconds)).isoformat()
            harness = rf"""
$script:stopped=$false
function Get-ScheduledTask {{ param([string]$TaskName,[object]$ErrorAction) [pscustomobject]@{{State='Running'}} }}
function Get-ScheduledTaskInfo {{ param([string]$TaskName,[object]$ErrorAction) [pscustomobject]@{{LastRunTime=[DateTime]::Parse('{last_run}')}} }}
function Get-CimInstance {{
 param([string]$ClassName,[string]$Filter,[object]$ErrorAction)
 if($Filter) {{
  if($script:stopped) {{ return @() }}
  return [pscustomobject]@{{ProcessId=2222;ExecutablePath='C:\runtime\child.exe';CommandLine='"C:\runtime\child.exe" -B "{release}" --database-path "{database}" --project-id "p"'}}
 }}
 if($script:stopped) {{ return @() }}
 return [pscustomobject]@{{ProcessId=1111;ExecutablePath='{python}';CommandLine='"{python}" -B "{release}" --database-path "{database}" --project-id "p"'}}
}}
function Stop-ScheduledTask {{ param([string]$TaskName,[object]$ErrorAction) $script:stopped=$true; Write-Output 'STOP_CALLED' }}
function Start-ScheduledTask {{ param([string]$TaskName,[object]$ErrorAction) Write-Output 'START_CALLED' }}
& '{script}' -MainTaskName 'main' -ProjectId 'p' -ReleaseRuntimeScript '{release}' -PythonExecutable '{python}' -DatabasePath '{database}' -HealthPath '{health_path}' -MaxHealthAgeSeconds 90 -StartupGraceSeconds 60
"""
            return subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", harness],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

    def test_fresh_health_keeps_exact_runtime_running(self):
        result = self._run(heartbeat_age_seconds=5, last_run_age_seconds=600)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("HEALTHY_RUNNING", result.stdout)
        self.assertNotIn("STOP_CALLED", result.stdout)
        self.assertNotIn("START_CALLED", result.stdout)

    def test_stale_health_restarts_running_task_without_direct_process_kill(self):
        result = self._run(heartbeat_age_seconds=180, last_run_age_seconds=600)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("STOP_CALLED", result.stdout)
        self.assertIn("START_CALLED", result.stdout)
        self.assertIn("RECOVERY_START_REQUESTED", result.stdout)
        self.assertIn("HEALTH_STALE", result.stdout)

    def test_startup_grace_does_not_restart_before_first_fresh_health(self):
        result = self._run(heartbeat_age_seconds=180, last_run_age_seconds=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("STARTING", result.stdout)
        self.assertNotIn("STOP_CALLED", result.stdout)
        self.assertNotIn("START_CALLED", result.stdout)


if __name__ == "__main__":
    unittest.main()
