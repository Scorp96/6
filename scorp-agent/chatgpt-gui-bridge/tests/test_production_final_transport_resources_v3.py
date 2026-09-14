import importlib.util
import pathlib
import unittest


TOOL_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "production_active_root_e2e_v3.py"
spec = importlib.util.spec_from_file_location("production_active_root_e2e_v3", TOOL_PATH)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


class ProductionFinalTransportResourcesV3Tests(unittest.TestCase):
    def _chrome_row(self, turn_id, actor_kind, submission_id, url):
        return {
            "state": "COMPLETED",
            "submission_id": submission_id,
            "conversation_url": url,
            "handle": {
                "submission_id": submission_id,
                "turn_id": turn_id,
                "actor_kind": actor_kind,
                "conversation_url": url,
            },
        }

    def test_completed_chrome_use_resources_pass_without_hwnd(self):
        rows = {
            "turn-master": self._chrome_row(
                "turn-master",
                "MASTER",
                "submit-master",
                "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111",
            ),
            "turn-worker": self._chrome_row(
                "turn-worker",
                "WORKER",
                "submit-worker",
                "https://chatgpt.com/c/22222222-2222-2222-2222-222222222222",
            ),
        }
        proof = canary._verify_transport_resources(rows, is_window=lambda hwnd: False)
        self.assertEqual("chrome-use", proof["kind"])
        self.assertEqual(2, proof["resource_count"])
        self.assertEqual(2, len(proof["conversation_urls"]))

    def test_chrome_use_resource_requires_completed_state(self):
        rows = {
            "turn-master": self._chrome_row(
                "turn-master",
                "MASTER",
                "submit-master",
                "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111",
            )
        }
        rows["turn-master"]["state"] = "SUBMITTING"
        with self.assertRaisesRegex(RuntimeError, "FINAL_E2E_CHROME_USE_TRANSPORT_NOT_COMPLETED"):
            canary._verify_transport_resources(rows, is_window=lambda hwnd: False)

    def test_chrome_use_resource_requires_exact_durable_binding(self):
        rows = {
            "turn-master": self._chrome_row(
                "turn-master",
                "MASTER",
                "submit-master",
                "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111",
            )
        }
        del rows["turn-master"]["handle"]["conversation_url"]
        with self.assertRaisesRegex(RuntimeError, "FINAL_E2E_CHROME_USE_HANDLE_INVALID"):
            canary._verify_transport_resources(rows, is_window=lambda hwnd: False)

    def test_legacy_closed_hwnd_resources_remain_supported(self):
        rows = {
            "turn-1": {"state": "COMPLETED", "handle": {"window_handle": 1001}},
            "turn-2": {"state": "COMPLETED", "handle": {"window_handle": 1002}},
        }
        proof = canary._verify_transport_resources(rows, is_window=lambda hwnd: False)
        self.assertEqual("windows-mcp", proof["kind"])
        self.assertEqual(2, proof["resource_count"])

    def test_legacy_live_hwnd_still_fails_closed(self):
        rows = {"turn-1": {"state": "COMPLETED", "handle": {"window_handle": 1001}}}
        with self.assertRaisesRegex(RuntimeError, "FINAL_E2E_WINDOW_LEAK"):
            canary._verify_transport_resources(rows, is_window=lambda hwnd: True)


if __name__ == "__main__":
    unittest.main()
