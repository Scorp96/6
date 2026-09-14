import asyncio
import contextlib
import os
import unittest
from unittest.mock import patch

import windows_mcp_actor_driver_v3 as driver_module
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3


class TextResult:
    def __init__(self, text, error=False):
        self.isError = error
        self.content = [type("Item", (), {"type": "text", "text": text})()]


class FakeClient:
    def __init__(self, snapshots=None, clipboard_reads=None):
        self.snapshots = list(snapshots or [])
        self.clipboard_reads = list(clipboard_reads or [])
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "Snapshot":
            text = self.snapshots.pop(0) if self.snapshots else ""
            return TextResult(text)
        if name == "Clipboard":
            if args.get("mode") == "get":
                return TextResult(self.clipboard_reads.pop(0) if self.clipboard_reads else "Clipboard content:\n")
            if args.get("mode") == "set":
                return TextResult("ok")
        if name in {"Wait", "Shortcut"}:
            return TextResult("ok")
        raise AssertionError(f"UNEXPECTED_TOOL:{name}")


class Factory:
    def __init__(self, clients):
        self.clients = list(clients)

    @contextlib.asynccontextmanager
    async def __call__(self):
        if not self.clients:
            raise AssertionError("NO_FAKE_CLIENT")
        yield self.clients.pop(0)


class WindowsMcpActorDriverV3Tests(unittest.TestCase):
    def test_windows_mcp_environment_expands_tree_budget_without_overriding_explicit_value(self):
        with patch.dict(os.environ, {}, clear=True):
            env = driver_module._windows_mcp_environment()
            self.assertGreaterEqual(int(env["WINDOWS_MCP_MAX_TREE_ELEMENTS"]), 2000)
            self.assertEqual("false", env["ANONYMIZED_TELEMETRY"])
            self.assertEqual("1", env["WINDOWS_MCP_DISABLE_FLASH"])
            self.assertEqual("utf-8", env["PYTHONIOENCODING"])
        with patch.dict(os.environ, {"WINDOWS_MCP_MAX_TREE_ELEMENTS": "5000"}, clear=True):
            env = driver_module._windows_mcp_environment()
            self.assertEqual("5000", env["WINDOWS_MCP_MAX_TREE_ELEMENTS"])

    def test_submit_returns_when_turn_and_focused_address_are_bound_without_waiting_for_response_marker(self):
        url = "https://chatgpt.com/c/worker-one"
        client = FakeClient(
            ["Focused Window: Chrome\nTURN_ID=turn-1\nStop generating\n"],
            ["Clipboard content:\noriginal", f"Clipboard content:\n{url}"],
        )
        submitted = []

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            submitted.append((client_arg, prompt, conversation_url))
            return "submitted"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            url_timeout_seconds=2,
            url_poll_seconds=0,
        )
        snapshot = asyncio.run(driver.submit_prompt(
            prompt="PROMPT",
            turn_id="turn-1",
            actor_kind="WORKER",
            conversation_url=None,
        ))
        self.assertIn(url, snapshot)
        self.assertNotIn("SCORP_GUI_ACTOR_V3", snapshot)
        self.assertEqual(1, len(submitted))
        self.assertEqual(1, sum(1 for name, _ in client.calls if name == "Snapshot"))

    def test_snapshot_conversation_uses_exact_url_and_closes_only_poll_window(self):
        url = "https://chatgpt.com/c/worker-one"
        client = FakeClient(
            snapshots=[f"Focused Window: Chrome\n{url}\nStop generating"],
            clipboard_reads=[
                "Clipboard content:\noriginal",
                f"Clipboard content:\n{url}",
            ],
        )
        acquired = []

        async def acquire(client_arg, wait_seconds=5, conversation_url=None):
            acquired.append((client_arg, conversation_url))
            return f"Focused Window: Chrome\n{conversation_url}\nStop generating"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            acquire_fn=acquire,
        )
        snapshot = asyncio.run(driver.snapshot_conversation(url))
        self.assertIn(url, snapshot)
        self.assertEqual([(client, url)], acquired)
        self.assertTrue(any(name == "Shortcut" and args.get("shortcut") == "alt+f4" for name, args in client.calls))
        self.assertTrue(any(name == "Clipboard" and args.get("mode") == "set" and args.get("text") == "original" for name, args in client.calls))

    def test_snapshot_conversation_scrolls_to_bottom_before_fresh_snapshot(self):
        url = "https://chatgpt.com/c/poll-bottom"
        marker = "SCORP_GUI_ACTOR_V3::turn-bottom::encoded"
        client = FakeClient(
            snapshots=[f"Focused Window: Chrome\n{marker}\n"],
            clipboard_reads=[
                "Clipboard content:\noriginal",
                f"Clipboard content:\n{url}",
            ],
        )

        async def acquire(client_arg, wait_seconds=5, conversation_url=None):
            self.assertEqual(url, conversation_url)
            return "Focused Window: Chrome\nbaseline-without-response-marker\n"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            acquire_fn=acquire,
        )
        snapshot = asyncio.run(driver.snapshot_conversation(url))
        self.assertIn(marker, snapshot)
        ctrl_end = next(
            index for index, (name, args) in enumerate(client.calls)
            if name == "Shortcut" and args.get("shortcut") == "ctrl+end"
        )
        fresh_snapshot = next(index for index, (name, _) in enumerate(client.calls) if name == "Snapshot")
        self.assertLess(ctrl_end, fresh_snapshot)

    def test_recover_only_accepts_current_focused_window_with_bound_turn_marker(self):
        url = "https://chatgpt.com/c/recover-one"
        client = FakeClient(
            ["Focused Window: Google Chrome\nTURN_ID=turn-1\nStop generating"],
            ["Clipboard content:\noriginal", f"Clipboard content:\n{url}"],
        )
        driver = WindowsMcpActorDriverV3(session_factory=Factory([client]))
        found = asyncio.run(driver.discover_turn_conversations("turn-1"))
        self.assertEqual(1, len(found))
        self.assertEqual(url, found[0]["conversation_url"])

    def test_recover_does_not_fuzzy_switch_or_guess_unbound_window(self):
        client = FakeClient(["Opened Windows: Chrome - similar title\nhttps://chatgpt.com/c/not-bound\n"])
        driver = WindowsMcpActorDriverV3(session_factory=Factory([client]))
        found = asyncio.run(driver.discover_turn_conversations("turn-1"))
        self.assertEqual([], found)
        self.assertFalse(any(name == "App" for name, _ in client.calls))

    def test_submit_without_bound_turn_times_out_without_address_probe(self):
        client = FakeClient(["Focused Window: Chrome"] * 3)

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            return "submitted"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            url_timeout_seconds=0.001,
            url_poll_seconds=0,
        )
        with self.assertRaisesRegex(TimeoutError, "ACTOR_GUI_URL_TIMEOUT"):
            asyncio.run(driver.submit_prompt(prompt="PROMPT", turn_id="turn-1", actor_kind="WORKER", conversation_url=None))
        self.assertFalse(any(name == "Clipboard" for name, _ in client.calls))


if __name__ == "__main__":
    unittest.main()
