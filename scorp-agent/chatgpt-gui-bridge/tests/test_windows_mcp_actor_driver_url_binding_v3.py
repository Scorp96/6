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


class WindowsMcpActorDriverUrlBindingV3Tests(unittest.TestCase):
    def test_new_chat_ignores_preexisting_global_urls_and_uses_focused_address(self):
        old = "https://chatgpt.com/c/old-existing-chat"
        new = "https://chatgpt.com/c/new-turn-chat"
        client = FakeClient(
            [f"Focused Window: Chrome\nTURN_ID=turn-1\nOpened Windows:\n{old}\n"],
            ["Clipboard content:\noriginal", f"Clipboard content:\n{new}"],
        )

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            self.assertIsNone(conversation_url)
            return f"Focused Window: Chrome\nOpened Windows:\n{old}\n"

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
        self.assertEqual(new, extract_conversation_url(snapshot))
        self.assertIn("TURN_ID=turn-1", snapshot)

    def test_existing_session_uses_known_url_only_when_focused_address_matches(self):
        old = "https://chatgpt.com/c/old-existing-chat"
        expected = "https://chatgpt.com/c/master-session"
        client = FakeClient(
            [f"Focused Window: Chrome\nTURN_ID=master-turn-2\nOpened Windows:\n{old}\n"],
            ["Clipboard content:\noriginal", f"Clipboard content:\n{expected}"],
        )

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            self.assertEqual(expected, conversation_url)
            return f"Focused Window: Chrome\nOpened Windows:\n{old}\n"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            url_timeout_seconds=2,
            url_poll_seconds=0,
        )
        snapshot = asyncio.run(driver.submit_prompt(
            prompt="PROMPT",
            turn_id="master-turn-2",
            actor_kind="MASTER",
            conversation_url=expected,
        ))
        self.assertEqual(expected, extract_conversation_url(snapshot))

    def test_new_chat_waits_past_transient_web_route_for_canonical_handle(self):
        transient = "https://chatgpt.com/c/WEB"
        canonical = "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"
        marker = "Focused Window: Chrome\nTURN_ID=turn-web-transient\n"
        client = FakeClient(
            [marker, marker],
            [
                "Clipboard content:\noriginal-1",
                f"Clipboard content:\n{transient}",
                "Clipboard content:\noriginal-2",
                f"Clipboard content:\n{canonical}",
            ],
        )

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            self.assertIsNone(conversation_url)
            return "Focused Window: Chrome\n"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            url_timeout_seconds=2,
            url_poll_seconds=0,
        )
        snapshot = asyncio.run(driver.submit_prompt(
            prompt="PROMPT",
            turn_id="turn-web-transient",
            actor_kind="MASTER",
            conversation_url=None,
        ))
        self.assertEqual(canonical, extract_conversation_url(snapshot))
        self.assertEqual(2, sum(1 for name, _ in client.calls if name == "Snapshot"))

    def test_focused_address_fails_closed_when_clipboard_wrapper_contains_two_chat_urls(self):
        one = "https://chatgpt.com/c/new-one"
        two = "https://chatgpt.com/c/new-two"
        client = FakeClient(
            ["Focused Window: Chrome\nTURN_ID=turn-3\n"],
            ["Clipboard content:\noriginal", f"Clipboard content:\n{one}\n{two}"],
        )

        async def open_submit(client_arg, prompt, page_wait_seconds=5, conversation_url=None):
            return "Focused Window: Chrome\n"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            url_timeout_seconds=2,
            url_poll_seconds=0,
        )
        with self.assertRaisesRegex(ValueError, "ACTOR_GUI_FOCUSED_URL_AMBIGUOUS"):
            asyncio.run(driver.submit_prompt(
                prompt="PROMPT",
                turn_id="turn-3",
                actor_kind="WORKER",
                conversation_url=None,
            ))


if __name__ == "__main__":
    unittest.main()
