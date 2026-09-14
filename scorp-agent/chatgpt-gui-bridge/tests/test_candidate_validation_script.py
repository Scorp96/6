import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]


class CandidateValidationScriptTests(unittest.TestCase):
    def test_validation_script_honors_scorp_python_override(self):
        text = (ROOT / 'scripts' / 'run-candidate-validation.ps1').read_text(encoding='utf-8')
        self.assertRegex(text, r'\$Python\s*=\s*if\s*\(\$env:SCORP_PYTHON\)')
        self.assertNotRegex(text, r'&\s+python\s+-B')
        self.assertIn('& $Python -B -m unittest', text)


if __name__ == '__main__':
    unittest.main()
