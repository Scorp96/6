import asyncio
import contextlib
import unittest

from gui_transport import ChatGptThrottleError, open_new_chat_and_submit
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
                return TextResult(self.clipboard_reads.pop(0) if self.clipboard_reads else "Clipboard content:\nhttps://chatgpt.com/")
            return TextResult("ok")
        if name in {"Wait", "Shortcut", "App", "Click", "Type"}:
            return TextResult("ok")
        raise AssertionError(f"UNEXPECTED_TOOL:{name}")


class Factory:
    def __init__(self, client):
        self.client = client

    @contextlib.asynccontextmanager
    async def __call__(self):
        yield self.client


class ActorThrottleRecoveryV3Tests(unittest.TestCase):
    def test_gui_transport_acknowledges_pre_input_rate_limit_then_waits_for_page_recovery(self):
        throttle = (
            "请求过于频繁，请稍等几分钟后再重试\n"
            "(410,520) button \"确定\""
        )
        recovered = '(200,700) textbox "Message ChatGPT"'
        send = '(900,700) button "Send message"'
        client = FakeClient(
            snapshots=[
                "Focused Window: Chrome",
                throttle,
                recovered,
                send,
            ],
            clipboard_reads=[
                "Clipboard content:\noriginal",
                "Clipboard content:\nhttps://chatgpt.com/",
            ],
        )
        asyncio.run(open_new_chat_and_submit(
            client,
            "PROMPT",
            page_wait_seconds=0,
            rate_limit_wait_seconds=0,
            rate_limit_poll_seconds=1,
            rate_limit_sleeper=lambda _: asyncio.sleep(0),
            rate_limit_clock=lambda: 0.0,
        ))
        self.assertEqual(
            1,
            sum(
                1 for name, args in client.calls
                if name == "Click" and args.get("loc") == [410, 520]
            ),
        )
        self.assertEqual(1, sum(1 for name, _ in client.calls if name == "Type"))

    def test_gui_transport_refreshes_once_after_bounded_rate_limit_wait(self):
        throttle = (
            "请求过于频繁，请稍等几分钟后再重试\n"
            "(410,520) button \"确定\""
        )
        recovered = '(200,700) textbox "Message ChatGPT"'
        send = '(900,700) button "Send message"'
        client = FakeClient(
            snapshots=["Focused Window: Chrome", throttle, throttle, recovered, send],
            clipboard_reads=[
                "Clipboard content:\noriginal",
                "Clipboard content:\nhttps://chatgpt.com/",
            ],
        )
        ticks = iter([0.0, 6.0])
        asyncio.run(open_new_chat_and_submit(
            client,
            "PROMPT",
            page_wait_seconds=0,
            rate_limit_wait_seconds=5,
            rate_limit_poll_seconds=5,
            rate_limit_sleeper=lambda _: asyncio.sleep(0),
            rate_limit_clock=lambda: next(ticks),
        ))
        self.assertEqual(
            1,
            sum(1 for name, args in client.calls if name == "Click" and args.get("loc") == [410, 520]),
        )
        self.assertEqual(
            1,
            sum(1 for name, args in client.calls if name == "Shortcut" and args.get("shortcut") == "ctrl+r"),
        )
        self.assertEqual(1, sum(1 for name, _ in client.calls if name == "Type"))

    def test_gui_transport_classifies_throttle_before_any_prompt_input(self):
        throttle = "请求过于频繁，请稍等几分钟后再重试"
        client = FakeClient(
            snapshots=[
                "Focused Window:\nChatGPT - Google Chrome\nOpened Windows:",
                throttle,
            ],
            clipboard_reads=[
                "Clipboard content:\noriginal",
                "Clipboard content:\nhttps://chatgpt.com/",
                "Clipboard content:\noriginal",
            ],
        )
        with self.assertRaisesRegex(ChatGptThrottleError, "CHATGPT_REQUEST_THROTTLED"):
            asyncio.run(open_new_chat_and_submit(client, "PROMPT", page_wait_seconds=0))
        self.assertFalse(any(name == "Type" for name, _ in client.calls))
        self.assertFalse(any(name == "Click" for name, _ in client.calls))

    def test_driver_delegates_throttle_to_deterministic_supervisor_without_retry(self):
        client = FakeClient()
        attempts = []
        closed = []

        async def open_submit(*args, **kwargs):
            attempts.append(1)
            raise ChatGptThrottleError("CHATGPT_REQUEST_THROTTLED")

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory(client),
            open_submit_fn=open_submit,
            foreground_window_handle_fn=lambda: 111111,
            close_window_handle_fn=lambda hwnd: closed.append(hwnd) or True,
            throttle_backoff_seconds=0,
            throttle_max_attempts=3,
            url_poll_seconds=0,
        )
        with self.assertRaisesRegex(ChatGptThrottleError, "CHATGPT_REQUEST_THROTTLED"):
            asyncio.run(driver.submit_prompt(
                prompt="PROMPT", turn_id="turn-1", actor_kind="WORKER", conversation_url=None
            ))
        self.assertEqual(1, len(attempts))
        self.assertEqual([], closed)

    def test_post_submit_throttle_fails_fast_without_retry_or_close(self):
        throttle = "Too many requests. Please wait and try again."
        client = FakeClient(snapshots=[throttle])
        attempts = []
        closed = []

        async def open_submit(*args, **kwargs):
            attempts.append(1)
            return "submitted"

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory(client),
            open_submit_fn=open_submit,
            foreground_window_handle_fn=lambda: 444444,
            close_window_handle_fn=lambda hwnd: closed.append(hwnd) or True,
            url_timeout_seconds=0.001,
            url_poll_seconds=0,
        )
        with self.assertRaises(Exception) as caught:
            asyncio.run(driver.submit_prompt(
                prompt="PROMPT", turn_id="turn-post-throttle", actor_kind="MASTER", conversation_url=None
            ))
        self.assertEqual("ACTOR_GUI_POST_SUBMIT_THROTTLED", str(caught.exception))
        self.assertEqual(1, len(attempts))
        self.assertEqual([], closed)

    def test_unknown_editor_failure_is_not_retried_or_closed(self):
        client = FakeClient()
        attempts = []
        closed = []

        async def open_submit(*args, **kwargs):
            attempts.append(1)
            raise ValueError("CHAT_EDITOR_NOT_FOUND")

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory(client),
            open_submit_fn=open_submit,
            foreground_window_handle_fn=lambda: 333333,
            close_window_handle_fn=lambda hwnd: closed.append(hwnd) or True,
            throttle_backoff_seconds=0,
            throttle_max_attempts=2,
        )
        with self.assertRaisesRegex(ValueError, "CHAT_EDITOR_NOT_FOUND"):
            asyncio.run(driver.submit_prompt(
                prompt="PROMPT", turn_id="turn-1", actor_kind="WORKER", conversation_url=None
            ))
        self.assertEqual(1, len(attempts))
        self.assertEqual([], closed)


if __name__ == "__main__":
    unittest.main()
