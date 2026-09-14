import pathlib
import unittest


class RelayCdpOpenReadinessV1Tests(unittest.TestCase):
    def test_open_waits_for_chatgpt_location_before_reporting_success(self):
        bridge = pathlib.Path(__file__).resolve().parents[1] / "relay_cdp_bridge_v1.mjs"
        source = bridge.read_text(encoding="utf-8")

        self.assertIn("const readyUrl = await waitEvaluate", source)
        self.assertIn("RELAY_CDP_OPEN_URL_NOT_READY", source)
        self.assertIn("chatgpt\\.com", source)
        self.assertIn("data: {targetId, url: readyUrl}", source)


if __name__ == "__main__":
    unittest.main()
