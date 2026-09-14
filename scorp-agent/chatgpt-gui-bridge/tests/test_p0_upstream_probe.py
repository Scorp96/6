import unittest

from p0_upstream_probe import classify_candidate_b


class ProbeTests(unittest.TestCase):
    def test_chatgpt_use_wins_only_when_receipts_and_windows_backend_are_proven(self):
        evidence = {
            "chatgpt_use": {
                "present": True,
                "request_receipts": True,
                "conversation_record": True,
                "windows": True,
            },
            "chrome_use": {"present": True, "windows": True},
        }
        self.assertEqual("CHATGPT_USE", classify_candidate_b(evidence))

    def test_falls_back_to_chrome_use(self):
        evidence = {
            "chatgpt_use": {
                "present": False,
                "request_receipts": False,
                "conversation_record": False,
                "windows": False,
            },
            "chrome_use": {"present": True, "windows": True},
        }
        self.assertEqual("CHROME_USE_DIRECT", classify_candidate_b(evidence))

    def test_blocks_without_proven_candidate(self):
        self.assertEqual(
            "BLOCKED",
            classify_candidate_b({"chatgpt_use": {}, "chrome_use": {}}),
        )


if __name__ == "__main__":
    unittest.main()
