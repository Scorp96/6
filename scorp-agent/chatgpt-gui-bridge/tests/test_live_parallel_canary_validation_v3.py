import importlib.util
import pathlib
import unittest


TOOL_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "live_parallel_master_worker_canary_v3.py"
spec = importlib.util.spec_from_file_location("live_parallel_master_worker_canary_v3", TOOL_PATH)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


class LiveParallelCanaryValidationV3Tests(unittest.TestCase):
    def _response(self, prefix, summary):
        return {
            "kind": "HANDOFF",
            "evidence": [f"{prefix}-{i:03d}" for i in range(1, 241)],
            "summary": summary,
        }

    def test_worker_one_exact_evidence_and_summary_pass(self):
        result = canary._validate_worker_handoff(
            self._response("W1", "worker-one-complete"),
            canary.OBJ1,
        )
        self.assertEqual("W1", result["prefix"])
        self.assertEqual(240, result["evidence_count"])

    def test_worker_two_exact_evidence_and_summary_pass(self):
        result = canary._validate_worker_handoff(
            self._response("W2", "worker-two-complete"),
            canary.OBJ2,
        )
        self.assertEqual("W2", result["prefix"])
        self.assertEqual(240, result["evidence_count"])

    def test_summary_only_handoff_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_EVIDENCE_INVALID"):
            canary._validate_worker_handoff({"kind": "HANDOFF", "summary": "worker-one-complete"}, canary.OBJ1)

    def test_wrong_length_is_rejected(self):
        response = self._response("W1", "worker-one-complete")
        response["evidence"] = response["evidence"][:-1]
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_EVIDENCE_INVALID"):
            canary._validate_worker_handoff(response, canary.OBJ1)

    def test_wrong_order_or_duplicate_is_rejected(self):
        response = self._response("W1", "worker-one-complete")
        response["evidence"][100], response["evidence"][101] = response["evidence"][101], response["evidence"][100]
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_EVIDENCE_INVALID"):
            canary._validate_worker_handoff(response, canary.OBJ1)

    def test_wrong_summary_is_rejected(self):
        response = self._response("W2", "worker-two-complete")
        response["summary"] = "worker-one-complete"
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_SUMMARY_INVALID"):
            canary._validate_worker_handoff(response, canary.OBJ2)

    def test_non_handoff_and_unknown_objective_are_rejected(self):
        response = self._response("W1", "worker-one-complete")
        response["kind"] = "BLOCKER"
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_NOT_HANDOFF"):
            canary._validate_worker_handoff(response, canary.OBJ1)
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_OBJECTIVE_UNKNOWN"):
            canary._validate_worker_handoff(self._response("W1", "worker-one-complete"), "f" * 64)

    def test_overlap_probe_selects_latest_submitted_worker_not_turn_id_order(self):
        workers = [
            ("v3-worker-z-older", {"submitted_at": "2026-09-12T00:34:12.661870Z"}),
            ("v3-worker-a-newer", {"submitted_at": "2026-09-12T00:34:33.080887Z"}),
        ]
        turn_id, row = canary._select_overlap_probe_worker(workers)
        self.assertEqual("v3-worker-a-newer", turn_id)
        self.assertEqual("2026-09-12T00:34:33.080887Z", row["submitted_at"])

    def test_overlap_probe_rejects_missing_or_duplicate_submission_times(self):
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_SUBMITTED_AT_MISSING"):
            canary._select_overlap_probe_worker([
                ("w1", {"submitted_at": "2026-09-12T00:34:12.661870Z"}),
                ("w2", {}),
            ])
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WORKER_SUBMITTED_AT_AMBIGUOUS"):
            canary._select_overlap_probe_worker([
                ("w1", {"submitted_at": "2026-09-12T00:34:12.661870Z"}),
                ("w2", {"submitted_at": "2026-09-12T00:34:12.661870Z"}),
            ])

    def test_transport_window_verifier_accepts_four_closed_hwnds(self):
        rows = {
            f"turn-{i}": {"handle": {"window_handle": 1000 + i}}
            for i in range(4)
        }
        self.assertEqual(4, canary._verify_transport_windows_closed(rows, lambda hwnd: False))

    def test_transport_window_verifier_rejects_live_or_missing_hwnd(self):
        rows = {
            "turn-1": {"handle": {"window_handle": 1001}},
            "turn-2": {"handle": {"window_handle": 1002}},
        }
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WINDOW_LEAK"):
            canary._verify_transport_windows_closed(rows, lambda hwnd: hwnd == 1002)
        with self.assertRaisesRegex(RuntimeError, "LIVE_CANARY_WINDOW_HANDLE_MISSING"):
            canary._verify_transport_windows_closed({"turn-x": {"handle": {}}}, lambda hwnd: False)


if __name__ == "__main__":
    unittest.main()
