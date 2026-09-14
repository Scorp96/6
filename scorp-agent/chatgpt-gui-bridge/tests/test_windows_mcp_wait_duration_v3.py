import asyncio
import contextlib
import unittest
from unittest.mock import AsyncMock, patch

import gui_transport as gt
import windows_mcp_actor_driver_v3 as driver_module
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3


class TextResult:
    def __init__(self, text="ok"):
        self.isError = False
        self.content = [type("Item", (), {"type": "text", "text": text})()]


class RejectFractionalWaitClient:
    def __init__(self, *, snapshots=None, clipboard_reads=None):
        self.snapshots = list(snapshots or [])
        self.clipboard_reads = list(clipboard_reads or [])
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "Wait":
            duration = args.get("duration")
            if not isinstance(duration, int):
                raise ValueError(f"WAIT_DURATION_MUST_BE_INTEGER:{duration}")
            return TextResult()
        if name == "Snapshot":
            return TextResult(self.snapshots.pop(0))
        if name == "Clipboard":
            if args.get("mode") == "get":
                return TextResult(self.clipboard_reads.pop(0))
            return TextResult()
        if name in {"App", "Shortcut"}:
            return TextResult()
        raise AssertionError(f"UNEXPECTED_TOOL:{name}")


class Factory:
    def __init__(self, client):
        self.client = client

    @contextlib.asynccontextmanager
    async def __call__(self):
        yield self.client


class WindowsMcpWaitDurationV3Tests(unittest.TestCase):
    def test_acquisition_uses_local_sleep_for_subsecond_address_poll(self):
        root = "https://chatgpt.com/"
        wrong = "https://chatgpt.com/c/old-chat"
        client = RejectFractionalWaitClient(
            snapshots=["Focused Window:\nChatGPT - Google Chrome\nOpened Windows:"],
            clipboard_reads=[
                "Clipboard content:\noriginal",
                f"Clipboard content:\n{wrong}",
                f"Clipboard content:\n{root}",
            ],
        )
        sleeper = AsyncMock()
        with patch.object(gt.asyncio, "sleep", sleeper):
            snapshot = asyncio.run(gt.acquire_chatgpt_window(
                client,
                wait_seconds=0,
                conversation_url=None,
                address_timeout_seconds=1,
                address_poll_seconds=0.25,
                address_max_attempts=3,
            ))
        self.assertIn("Google Chrome", snapshot)
        sleeper.assert_awaited_with(0.25)
        self.assertFalse(any(
            name == "Wait" and not isinstance(args.get("duration"), int)
            for name, args in client.calls
        ))

    def test_driver_uses_local_sleep_for_subsecond_url_poll(self):
        turn_id = "fractional-wait-turn"
        url = "https://chatgpt.com/c/fractional-wait-chat"
        client = RejectFractionalWaitClient(
            snapshots=[
                "Focused Window:\nChatGPT - Google Chrome\nOpened Windows:",
                f"Focused Window:\nChatGPT - Google Chrome\nTURN_ID={turn_id}\nOpened Windows:",
            ],
            clipboard_reads=["Clipboard content:\noriginal", f"Clipboard content:\n{url}"],
        )

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            self.assertIs(client_arg, client)
            self.assertIsNone(conversation_url)
            return "submitted"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory(client),
            open_submit_fn=open_submit,
            url_timeout_seconds=2,
            url_poll_seconds=0.5,
        )
        sleeper = AsyncMock()
        with patch.object(driver_module.asyncio, "sleep", sleeper):
            snapshot = asyncio.run(driver.submit_prompt(
                prompt="PROMPT",
                turn_id=turn_id,
                actor_kind="MASTER",
                conversation_url=None,
            ))
        self.assertIn(turn_id, snapshot)
        sleeper.assert_awaited_with(0.5)
        self.assertFalse(any(
            name == "Wait" and not isinstance(args.get("duration"), int)
            for name, args in client.calls
        ))


if __name__ == "__main__":
    unittest.main()
