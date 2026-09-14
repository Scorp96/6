from __future__ import annotations

import pathlib
import tempfile
import unittest

from tools.v4_live_two_worker_canary import failure_evidence, main


class V4LiveTwoWorkerCanaryTests(unittest.TestCase):
    def test_send_gate_refuses_without_explicit_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            result = main(
                [
                    "--database-path", str(root / "state.sqlite3"),
                    "--driver-state-path", str(root / "driver.json"),
                    "--allowed-root", str(root),
                    "--evidence-path", str(root / "evidence.json"),
                ]
            )
            self.assertEqual(2, result)
            self.assertFalse((root / "evidence.json").exists())

    def test_failure_evidence_is_blocked_and_records_ambiguous_intent_without_retry(self):
        receipt = failure_evidence(
            project_id="p",
            error=RuntimeError("daemon busy"),
            intents=[
                {"intent_id": "i", "state": "MAY_HAVE_SUBMITTED", "conversation_url": None},
            ],
        )
        self.assertEqual("BLOCKED", receipt["result"])
        self.assertEqual("RuntimeError", receipt["error_type"])
        self.assertEqual(0, receipt["retry_count"])
        self.assertEqual("MAY_HAVE_SUBMITTED", receipt["intents"][0]["state"])


if __name__ == "__main__":
    unittest.main()
