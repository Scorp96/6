from __future__ import annotations

import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]


class ObserverCanaryTaskSourceTests(unittest.TestCase):
    def test_launcher_no_send_calls_only_declared_one_shot_observer(self):
        content=(ROOT/"run_isolated_observer_canary.ps1").read_text(encoding="utf-8")
        self.assertIn("master_a_dynamic_v4.local_tick_observer",content)
        self.assertIn("PINNED_SHA_MISMATCH",content)
        self.assertIn("ISOLATED_CHECKOUT_DIRTY",content)
        self.assertIn("OBSERVER_VIOLATED_NO_SEND",content)
        for prohibited in ("Start-ScheduledTask","Stop-ScheduledTask",
                           "Register-ScheduledTask","Unregister-ScheduledTask",
                           "Enable-ScheduledTask","tab select","tab adopt",
                           "submit_prompt("):
            self.assertNotIn(prohibited,content)
        self.assertIn("mode",content.lower())

    def test_installer_has_read_only_and_disabled_default(self):
        script=(ROOT/"install_isolated_observer_canary.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$Mode = 'Inspect'",script)
        self.assertIn("'InstallDisabled'",script)
        self.assertIn("Disable-ScheduledTask -TaskName $taskName",script)
        self.assertIn("ISOLATED_TASK_ACTION_MISMATCH",script)
        self.assertIn("ISOLATED_TASK_NAME_COLLISION",script)
        self.assertIn("MultipleInstances IgnoreNew",script)
        self.assertIn("New-ScheduledTaskSettingsSet -Disable",script)
        self.assertIn("RestartCount 0",script)
        self.assertIn("LogonType Interactive -RunLevel Limited",script)
        self.assertNotIn("ScorpChatGptGuiBridgeWatchdog",script)
        self.assertNotIn("ScorpV4PersistentRuntime",script)

    def test_scheduler_can_be_disabled_without_valid_checkout(self):
        script=(ROOT/"install_isolated_observer_canary.ps1").read_text(encoding="utf-8")
        self.assertIn("if ($Mode -in @('DryRun','InstallDisabled','Enable'))",script)
        self.assertIn("if ($Mode -eq 'Disable')",script)
        self.assertIn("DISABLE_ISOLATED_TASK_BEFORE_REMOVAL",script)

    def test_not_an_unauthorized_chrome_sender(self):
        body=(ROOT/"run_isolated_observer_canary.ps1").read_text(encoding="utf-8")
        args=(
            "--v3-project-root","--gui-health-file","--r1-state-db",
            "--chrome-use-executable","--interval-minutes",
        )
        for arg in args:
            self.assertIn(arg,body)
        self.assertIn("C:\\ScorpAgent\\experiments",body)


if __name__=="__main__":
    unittest.main()
