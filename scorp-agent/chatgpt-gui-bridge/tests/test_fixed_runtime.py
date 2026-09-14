import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class FixedRuntimeTests(unittest.TestCase):
    def test_scheduled_task_executes_fixed_python_directly(self):
        text=(ROOT/'install-bridge.ps1').read_text(encoding='utf-8')
        self.assertIn("chatgpt-gui-bridge-runtime", text)
        self.assertIn("Scripts\\python.exe", text)
        self.assertIn("New-ScheduledTaskAction -Execute $python", text)
        self.assertNotIn("New-ScheduledTaskAction -Execute $ps", text)

    def test_runtime_dependency_is_pinned(self):
        text=(ROOT/'install-bridge.ps1').read_text(encoding='utf-8')
        self.assertIn("mcp==2.2.0", text)

    def test_installer_deploys_role_relay_module(self):
        text=(ROOT/'install-bridge.ps1').read_text(encoding='utf-8')
        self.assertIn("'role_relay.py'", text)

    def test_manual_runner_uses_fixed_python_not_uv_run(self):
        text=(ROOT/'run-bridge.ps1').read_text(encoding='utf-8')
        self.assertIn("chatgpt-gui-bridge-runtime\\Scripts\\python.exe", text)
        self.assertNotIn("run --with mcp", text)

if __name__=='__main__': unittest.main()
