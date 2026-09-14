from __future__ import annotations

import pathlib
import tempfile
import unittest

from tools.v4_live_two_worker_canary import main


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


if __name__ == "__main__":
    unittest.main()
