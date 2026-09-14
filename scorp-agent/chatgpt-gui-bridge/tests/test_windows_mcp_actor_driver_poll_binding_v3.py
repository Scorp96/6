import asyncio
import contextlib
import unittest

from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3


class TextResult:
    def __init__(self, text, error=False):
        self.isError = error
        self.content = [type("Item", (), {"type": "text", "text": text})()]


class FakeClient:
    def __init__(self, clipboard_reads=None, snapshots=None):
        self.clipboard_reads = list(clipboard_reads or [])
        self.snapshots = list(snapshots or [])
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "Clipboard":
            if args.get("mode") == "get":
                return TextResult(self.clipboard_reads.pop(0) if self.clipboard_reads else "Clipboard content:\n")
            if args.get("mode") == "set":
                return TextResult("ok")
        if name == "Snapshot":
            return TextResult(self.snapshots.pop(0) if self.snapshots else "Focused Window: Google Chrome\n")
        if name in {"Shortcut", "Wait"}:
            return TextResult("ok")
        raise AssertionError(f"UNEXPECTED_TOOL:{name}")


class Factory:
    def __init__(self, client):
        self.client = client

    @contextlib.asynccontextmanager
    async def __call__(self):
        yield self.client


class WindowsMcpActorDriverPollBindingV3Tests(unittest.TestCase):
    def test_snapshot_conversation_fails_closed_when_focused_address_is_not_requested_url(self):
        requested = "https://chatgpt.com/c/requested-worker"
        wrong = "https://chatgpt.com/c/wrong-focused-window"
        client = FakeClient(
            [
                "Clipboard content:\nuser-original",
                f"Clipboard content:\n{wrong}",
            ],
            ["Focused Window: Google Chrome\nSCORP_GUI_ACTOR_V3::turn-x::payload"],
        )

        async def acquire(client_arg, wait_seconds=5, conversation_url=None):
            self.assertEqual(requested, conversation_url)
            return "Focused Window: Google Chrome\nSCORP_GUI_ACTOR_V3::turn-x::payload"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory(client),
            acquire_fn=acquire,
        )
        with self.assertRaisesRegex(ValueError, "ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH"):
            asyncio.run(driver.snapshot_conversation(requested))
        self.assertTrue(any(
            name == "Clipboard"
            and args.get("mode") == "set"
            and args.get("text") == "user-original"
            for name, args in client.calls
        ))
        self.assertTrue(any(
            name == "Shortcut" and args.get("shortcut") == "alt+f4"
            for name, args in client.calls
        ))


if __name__ == "__main__":
    unittest.main()
