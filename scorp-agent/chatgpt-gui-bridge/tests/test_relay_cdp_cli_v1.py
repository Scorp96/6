import asyncio
import json
import pathlib
import tempfile
import unittest

from relay_cdp_cli_v1 import RelayCdpCliV1


class RelayCdpCliV1Tests(unittest.TestCase):
    def test_run_json_preserves_logical_session_and_command_shape(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append((list(argv), timeout_seconds))
            return 0, json.dumps({"data": {"url": "https://chatgpt.com/"}}), ""

        with tempfile.TemporaryDirectory() as td:
            cli = RelayCdpCliV1(
                node_executable="node.exe",
                helper_path="relay_cdp_bridge_v1.mjs",
                state_path=pathlib.Path(td) / "relay-state.json",
                runner=runner,
            )
            result = asyncio.run(cli.run_json("logical-session", "get", "url", timeout_seconds=7))

        self.assertEqual({"data": {"url": "https://chatgpt.com/"}}, result)
        argv, timeout = calls[0]
        self.assertEqual(7, timeout)
        self.assertEqual("node.exe", argv[0])
        self.assertEqual("relay_cdp_bridge_v1.mjs", argv[1])
        self.assertEqual("logical-session", argv[3])
        self.assertEqual(["get", "url"], argv[4:])

    def test_nonzero_helper_exit_is_fail_closed(self):
        async def runner(argv, timeout_seconds):
            return 9, "", "RELAY_CDP_TARGET_GONE"

        with tempfile.TemporaryDirectory() as td:
            cli = RelayCdpCliV1(
                node_executable="node.exe",
                helper_path="relay_cdp_bridge_v1.mjs",
                state_path=pathlib.Path(td) / "relay-state.json",
                runner=runner,
            )
            with self.assertRaisesRegex(RuntimeError, "RELAY_CDP_EXIT_9"):
                asyncio.run(cli.run_json("logical-session", "read", timeout_seconds=5))

    def test_invalid_json_is_rejected(self):
        async def runner(argv, timeout_seconds):
            return 0, "not-json", ""

        with tempfile.TemporaryDirectory() as td:
            cli = RelayCdpCliV1(
                node_executable="node.exe",
                helper_path="relay_cdp_bridge_v1.mjs",
                state_path=pathlib.Path(td) / "relay-state.json",
                runner=runner,
            )
            with self.assertRaisesRegex(ValueError, "RELAY_CDP_INVALID_JSON"):
                asyncio.run(cli.run_json("logical-session", "read", timeout_seconds=5))


if __name__ == "__main__":
    unittest.main()
