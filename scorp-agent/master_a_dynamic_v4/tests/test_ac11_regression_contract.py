from __future__ import annotations

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[3]
RECEIPT = ROOT / "docs" / "handoffs" / "SCORP_V4_VERIFICATION.json"


class RegressionContractTests(unittest.TestCase):
    def test_verification_receipt_covers_required_suites_without_false_soak_claim(self):
        value = json.loads(RECEIPT.read_text(encoding="utf-8"))
        self.assertEqual("scorp-v4-verification/1", value["format"])
        commands = {item["id"]: item for item in value["commands"]}
        required = {
            "v4_unit",
            "bridge_full_regression",
            "python_compile",
            "git_diff_check",
            "powershell_v4_critical",
            "powershell_v4_production",
            "powershell_v4_selfheal",
            "reference_scan",
        }
        self.assertTrue(required.issubset(commands))
        for command_id in required:
            item = commands[command_id]
            self.assertTrue(str(item["command"]).strip())
            self.assertEqual(0, item["exit_code"], command_id)
            self.assertTrue(str(item["observed"]).strip())
        self.assertGreaterEqual(commands["v4_unit"]["tests_run"], 41)
        self.assertEqual(397, commands["bridge_full_regression"]["tests_run"])
        self.assertIn(value["acceptance"]["AC12"], {"NOT_RUN", "PASS_SHORT_SOAK_PERFORMANCE"})
        self.assertFalse(value["long_duration_stability_proven"])
        self.assertFalse(value["production_mutated"])
        self.assertFalse(value["production_cutover_authorized"])
        self.assertEqual("SOFT_DEPRECATE", value["deprecation_policy"])


if __name__ == "__main__":
    unittest.main()
