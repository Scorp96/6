import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from chrome_use_cli_v3 import ChromeUseCliV3
from p0_live_chatgpt_probe import build_cli
from relay_cdp_cli_v1 import RelayCdpCliV1


class P0LiveTransportSelectionTests(unittest.TestCase):
    def test_default_transport_remains_chrome_use(self):
        args = SimpleNamespace(executable="chrome-use.exe")
        cli = build_cli(args)
        self.assertIsInstance(cli, ChromeUseCliV3)

    def test_relay_transport_builds_stateless_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            state = str(Path(td) / "relay.json")
            args = SimpleNamespace(
                executable="chrome-use.exe",
                transport="relay-cdp",
                node_executable="node.exe",
                relay_helper="relay_cdp_bridge_v1.mjs",
                relay_state_path=state,
            )
            cli = build_cli(args)
        self.assertIsInstance(cli, RelayCdpCliV1)
        self.assertEqual("relay_cdp_bridge_v1.mjs", cli.helper_path)
        self.assertEqual(state, cli.state_path)

    def test_relay_transport_missing_config_fails_closed(self):
        args = SimpleNamespace(
            executable="chrome-use.exe",
            transport="relay-cdp",
            node_executable="node.exe",
            relay_helper=None,
            relay_state_path=None,
        )
        with self.assertRaisesRegex(ValueError, "LIVE_RELAY_CDP_CONFIG_MISSING"):
            build_cli(args)


if __name__ == "__main__":
    unittest.main()
