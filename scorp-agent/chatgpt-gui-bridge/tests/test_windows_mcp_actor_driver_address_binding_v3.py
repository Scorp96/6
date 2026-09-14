import asyncio
import contextlib
import unittest

from gui_transport import extract_conversation_url
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
            return TextResult(self.snapshots.pop(0) if self.snapshots else "")
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


class WindowsMcpActorDriverAddressBindingV3Tests(unittest.TestCase):
    def test_new_turn_uses_focused_address_bar_not_snapshot_global_urls(self):
        correct = "https://chatgpt.com/c/new-focused-turn"
        wrong = "https://chatgpt.com/c/old-global-chat"
        client = FakeClient(
            snapshots=[f"Focused Window: Chrome\nTURN_ID=turn-address-1\nOpened Windows:\n{wrong}\n"],
            clipboard_reads=["Clipboard content:\nuser-original", f"Clipboard content:\n{correct}"],
        )

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            self.assertIsNone(conversation_url)
            return f"Focused Window: Chrome\nOpened Windows:\n{wrong}\n"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            url_timeout_seconds=2,
            url_poll_seconds=0,
        )
        snapshot = asyncio.run(driver.submit_prompt(
            prompt="PROMPT",
            turn_id="turn-address-1",
            actor_kind="WORKER",
            conversation_url=None,
        ))
        self.assertEqual(correct, extract_conversation_url(snapshot))
        self.assertIn("TURN_ID=turn-address-1", snapshot)
        self.assertTrue(any(name == "Shortcut" and args.get("shortcut") == "ctrl+l" for name, args in client.calls))
        self.assertTrue(any(name == "Shortcut" and args.get("shortcut") == "ctrl+c" for name, args in client.calls))
        self.assertTrue(any(name == "Clipboard" and args.get("mode") == "set" and args.get("text") == "user-original" for name, args in client.calls))

    def test_existing_session_requires_address_bar_to_match_known_url(self):
        expected = "https://chatgpt.com/c/master-known"
        wrong = "https://chatgpt.com/c/another-chat"
        client = FakeClient(
            snapshots=["Focused Window: Chrome\nTURN_ID=master-address-2\n"],
            clipboard_reads=["Clipboard content:\nuser-original", f"Clipboard content:\n{wrong}"],
        )

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            self.assertEqual(expected, conversation_url)
            return "Focused Window: Chrome\n"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            url_timeout_seconds=2,
            url_poll_seconds=0,
        )
        with self.assertRaisesRegex(ValueError, "ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH"):
            asyncio.run(driver.submit_prompt(
                prompt="PROMPT",
                turn_id="master-address-2",
                actor_kind="MASTER",
                conversation_url=expected,
            ))

    def test_recovery_uses_focused_address_bar_for_bound_turn(self):
        correct = "https://chatgpt.com/c/recovery-focused"
        wrong = "https://chatgpt.com/c/recovery-global-wrong"
        client = FakeClient(
            snapshots=[f"Focused Window: Google Chrome\nTURN_ID=recover-address-3\nOpened Windows:\n{wrong}\n"],
            clipboard_reads=["Clipboard content:\nuser-original", f"Clipboard content:\n{correct}"],
        )
        driver = WindowsMcpActorDriverV3(session_factory=Factory([client]))
        found = asyncio.run(driver.discover_turn_conversations("recover-address-3"))
        self.assertEqual(1, len(found))
        self.assertEqual(correct, found[0]["conversation_url"])
        self.assertTrue(any(name == "Clipboard" and args.get("mode") == "set" and args.get("text") == "user-original" for name, args in client.calls))

    def test_snapshot_conversation_fails_closed_when_focused_address_differs(self):
        expected = "https://chatgpt.com/c/poll-expected"
        wrong = "https://chatgpt.com/c/poll-wrong"
        client = FakeClient(
            clipboard_reads=["Clipboard content:\nuser-original", f"Clipboard content:\n{wrong}"],
        )

        async def acquire(client_arg, wait_seconds=2, conversation_url=None):
            self.assertEqual(expected, conversation_url)
            return "Focused Window: Chrome\nTURN_ID=poll-turn\n"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            acquire_fn=acquire,
        )
        with self.assertRaisesRegex(ValueError, "ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH"):
            asyncio.run(driver.snapshot_conversation(expected))
        self.assertTrue(any(name == "Shortcut" and args.get("shortcut") == "alt+f4" for name, args in client.calls))


if __name__ == "__main__":
    unittest.main()
