import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallContractTests(unittest.TestCase):
    def test_installer_pins_runtime_paths_and_pywin32(self):
        text = (ROOT / 'install-broker.ps1').read_text(encoding='utf-8')
        self.assertIn(r'C:\ScorpAgent\privileged-broker-runtime', text)
        self.assertIn(r'C:\ScorpAgent\privileged-broker', text)
        self.assertIn(r'C:\ProgramData\ScorpAgent\privileged-broker', text)
        self.assertIn('pywin32==312', text)
        self.assertIn('ScorpPrivilegedBroker', text)

    def test_installer_requires_localsystem_automatic_and_identity_verification(self):
        text = (ROOT / 'install-broker.ps1').read_text(encoding='utf-8')
        self.assertIn('LocalSystem', text)
        self.assertRegex(text, r"(?s)'start=',\s*'auto'")
        self.assertIn('identity.get', text)
        self.assertIn('S-1-5-18', text)
        self.assertIn('BROKER_INSTALL_PASS', text)

    def test_installer_preserves_state_and_has_rollback(self):
        text = (ROOT / 'install-broker.ps1').read_text(encoding='utf-8')
        self.assertIn('secret.key', text)
        self.assertIn('ledger.json', text)
        self.assertIn('audit.jsonl', text)
        self.assertIn('rollback', text.lower())
        self.assertIn('backup', text.lower())
        self.assertRegex(
            text,
            r"(?s)\$secretPath\s*=\s*Join-Path\s+\$StateDir\s+'secret\.key'.*?"
            r"if\s*\(-not\s*\(Test-Path\s+-LiteralPath\s+\$secretPath",
        )

    def test_sc_wrapper_does_not_shadow_powershell_args(self):
        text = (ROOT / 'install-broker.ps1').read_text(encoding='utf-8')
        self.assertNotIn('function Invoke-Sc([string[]]$Args)', text)
        self.assertRegex(text, r'function Invoke-Sc\(\[string\[\]\]\$ScArgs\)')

    def test_installer_keeps_acl_call_and_try_as_separate_tokens(self):
        text = (ROOT / 'install-broker.ps1').read_text(encoding='utf-8')
        self.assertNotIn('$userSidtry', text)
        self.assertRegex(text, r'Set-SecretAcl\s+\$secretPath\s+\$userSid\s+try\s*\{')

    def test_uninstaller_preserves_state_data(self):
        text = (ROOT / 'uninstall-broker.ps1').read_text(encoding='utf-8')
        self.assertIn('ScorpPrivilegedBroker', text)
        self.assertNotIn("Remove-Item -LiteralPath 'C:\\ProgramData\\ScorpAgent\\privileged-broker'", text)
        self.assertIn('PRESERVED', text)

    def test_service_host_is_explicit(self):
        service = (ROOT / 'broker_service.py').read_text(encoding='utf-8')
        self.assertIn("'--service-host'", service)
        self.assertIn('StartServiceCtrlDispatcher', service)


if __name__ == '__main__':
    unittest.main()
