import asyncio
import contextlib
import unittest

from actor_gui_backend_v3 import ActorGuiBackendV3
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
            return TextResult("ok")
        if name in {"Wait", "Shortcut"}:
            return TextResult("ok")
        raise AssertionError(f"UNEXPECTED_TOOL:{name}")


class Factory:
    def __init__(self, clients):
        self.clients = list(clients)

    @contextlib.asynccontextmanager
    async def __call__(self):
        yield self.clients.pop(0)


class WindowBoundText(str):
    def __new__(cls, value, window_handle):
        obj = str.__new__(cls, value)
        obj.window_handle = window_handle
        return obj


class BackendDriver:
    def __init__(self):
        self.poll_calls = []

    async def submit_prompt(self, **kwargs):
        return WindowBoundText(
            "https://chatgpt.com/c/hwnd-one\nTURN_ID=turn-1",
            424242,
        )

    async def snapshot_conversation(self, conversation_url, *, window_handle=None):
        self.poll_calls.append((conversation_url, window_handle))
        return "TURN_ID=turn-1\nStop generating"

    async def discover_turn_conversations(self, turn_id):
        return []


class WindowsMcpWindowHandleV3Tests(unittest.TestCase):
    def test_submit_preserves_string_contract_and_captures_foreground_hwnd(self):
        url = "https://chatgpt.com/c/hwnd-one"
        client = FakeClient(
            snapshots=["Focused Window: Chrome\nTURN_ID=turn-1\nStop generating"],
            clipboard_reads=["Clipboard content:\noriginal", f"Clipboard content:\n{url}"],
        )

        async def open_submit(*args, **kwargs):
            return "submitted"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            open_submit_fn=open_submit,
            foreground_window_handle_fn=lambda: 424242,
            url_poll_seconds=0,
        )
        result = asyncio.run(driver.submit_prompt(
            prompt="PROMPT", turn_id="turn-1", actor_kind="WORKER", conversation_url=None
        ))
        self.assertIsInstance(result, str)
        self.assertEqual(424242, result.window_handle)
        self.assertIn(url, result)

    def test_poll_with_hwnd_reuses_existing_window_without_acquire_or_close(self):
        url = "https://chatgpt.com/c/hwnd-one"
        client = FakeClient(
            snapshots=[f"Focused Window: Chrome\n{url}\nStop generating"],
            clipboard_reads=["Clipboard content:\noriginal", f"Clipboard content:\n{url}"],
        )
        acquired = []
        focused = []

        async def acquire(*args, **kwargs):
            acquired.append((args, kwargs))
            return "should-not-run"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory([client]),
            acquire_fn=acquire,
            focus_window_handle_fn=lambda hwnd: focused.append(hwnd) or True,
            window_focus_settle_seconds=0,
        )
        snapshot = asyncio.run(driver.snapshot_conversation(url, window_handle=424242))
        self.assertIn(url, snapshot)
        self.assertEqual([424242], focused)
        self.assertEqual([], acquired)
        self.assertFalse(any(
            name == "Shortcut" and args.get("shortcut") == "alt+f4"
            for name, args in client.calls
        ))

    def test_backend_persists_hwnd_and_passes_it_back_on_poll(self):
        driver = BackendDriver()
        backend = ActorGuiBackendV3(driver)
        handle = asyncio.run(backend.submit(
            "actor-submit-1",
            prompt="PROMPT",
            turn_id="turn-1",
            actor_kind="WORKER",
            conversation_url=None,
            timeout_seconds=10,
        ))
        self.assertEqual(424242, handle["window_handle"])
        result = asyncio.run(backend.poll(
            handle, turn_id="turn-1", actor_kind="WORKER", timeout_seconds=10
        ))
        self.assertEqual("PENDING", result["status"])
        self.assertEqual([("https://chatgpt.com/c/hwnd-one", 424242)], driver.poll_calls)


if __name__ == "__main__":
    unittest.main()