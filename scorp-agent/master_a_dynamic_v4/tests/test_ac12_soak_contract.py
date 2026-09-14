from __future__ import annotations

import copy
import pathlib
import tempfile
import unittest


class ShortSoakContractTests(unittest.TestCase):
    def valid_receipt(self):
        return {
            "format": "scorp-v4-short-soak-receipt/1",
            "status": "PASS",
            "requested_duration_seconds": 5.0,
            "actual_duration_seconds": 5.2,
            "started_at": "2026-09-13T00:00:00Z",
            "ended_at": "2026-09-13T00:00:05.200000Z",
            "task_cycles": 4,
            "tasks_verified": 12,
            "throughput_tasks_per_second": 2.307692,
            "concurrency_peak": 2,
            "slot_reuse_count": 6,
            "scheduler_recoveries": 2,
            "max_recovery_seconds": 0.08,
            "recovery_limit_seconds": 2.0,
            "browser_intents": 4,
            "submit_attempts": 4,
            "duplicate_submits": 0,
            "errors": 0,
            "environment": {
                "os": "Windows",
                "python": "3.x",
                "cpu_count": 8,
                "sqlite": "3.x",
                "sqlite_pragmas": {"journal_mode": "delete", "synchronous": 2, "foreign_keys": 1, "busy_timeout": 5000},
            },
            "long_duration_stability_proven": False,
        }

    def test_unexecuted_harness_is_not_run_and_does_not_claim_long_stability(self):
        from master_a_dynamic_v4.soak_harness import initial_not_run_receipt, validate_short_soak_receipt

        receipt = initial_not_run_receipt(requested_duration_seconds=10.0)
        self.assertEqual("NOT_RUN", receipt["status"])
        self.assertFalse(receipt["long_duration_stability_proven"])
        self.assertEqual("NOT_RUN", validate_short_soak_receipt(receipt))

    def test_false_short_soak_passes_are_rejected(self):
        from master_a_dynamic_v4.soak_harness import SoakEvidenceError, validate_short_soak_receipt

        variants = {
            "short": {"actual_duration_seconds": 4.9},
            "no-concurrency": {"concurrency_peak": 1},
            "no-reuse": {"slot_reuse_count": 0},
            "no-recovery": {"scheduler_recoveries": 0},
            "slow-recovery": {"max_recovery_seconds": 2.1},
            "duplicate": {"duplicate_submits": 1},
            "errors": {"errors": 1},
            "lost-task": {"tasks_verified": 11},
            "missing-env": {"environment": {}},
            "false-long-proof": {"long_duration_stability_proven": True},
        }
        for name, change in variants.items():
            with self.subTest(name=name):
                receipt = copy.deepcopy(self.valid_receipt())
                receipt.update(change)
                with self.assertRaises(SoakEvidenceError):
                    validate_short_soak_receipt(receipt)
        self.assertEqual("PASS", validate_short_soak_receipt(self.valid_receipt()))

    def test_benchmark_requires_real_positive_duration_and_isolated_database(self):
        from master_a_dynamic_v4.soak_harness import SoakEvidenceError, run_short_soak

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            with self.assertRaisesRegex(SoakEvidenceError, "SHORT_SOAK_DURATION_TOO_SMALL"):
                run_short_soak(root, duration_seconds=0)


if __name__ == "__main__":
    unittest.main()
