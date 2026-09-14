import asyncio
import contextlib
import unittest

from gui_transport import ChatGptThrottleError
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3


class TextResult:
    def __init__(self, text=""):
        self.isError = False
        self.content = [type("Item", (), {"type": "text", "text": text})()]


class FakeClient:
    async def call_tool(self, name, args):
        return TextResult("")


class Factory:
    @contextlib.asynccontextmanager
    async def __call__(self):
        yield FakeClient()


class DriverThrottleDelegationV3Tests(unittest.TestCase):
    def test_pre_submit_throttle_is_not_retried_or_closed_inside_driver(self):
        attempts = []
        closed = []

        async def open_submit(*args, **kwargs):
            attempts.append(1)
            raise ChatGptThrottleError("CHATGPT_REQUEST_THROTTLED")

        driver = WindowsMcpActorDriverV3(
            session_factory=Factory(),
            open_submit_fn=open_submit,
            foreground_window_handle_fn=lambda: 123456,
            close_window_handle_fn=lambda hwnd: closed.append(hwnd) or True,
            throttle_backoff_seconds=0,
            throttle_max_attempts=3,
        )
        with self.assertRaisesRegex(ChatGptThrottleError, "CHATGPT_REQUEST_THROTTLED"):
            asyncio.run(driver.submit_prompt(
                prompt="PROMPT",
                turn_id="turn-throttle-delegate",
                actor_kind="WORKER",
                conversation_url=None,
            ))
        self.assertEqual(1, len(attempts))
        self.assertEqual([], closed)


if __name__ == "__main__":
    unittest.main()
