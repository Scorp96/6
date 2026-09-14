import asyncio
import pathlib
import tempfile
import unittest

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3


class FakeCli:
    def __init__(self):
        self.calls = []
        self.responses = []

    async def run_json(self, session, *args, timeout_seconds=30):
        self.calls.append((session, list(args), timeout_seconds))
        if not self.responses:
            raise AssertionError(f"unexpected call: {session} {args}")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class ForegroundAwareFakeCli(FakeCli):
    async def prepare_interactive(self, session, *, timeout_seconds=30):
        self.calls.append((session, ["bringToFront"], timeout_seconds))
        return {"success": True}


class ChromeUseSendButtonV3Tests(unittest.TestCase):
    def test_driver_prepares_chrome_use_visibility_before_control_snapshots(self):
        with tempfile.TemporaryDirectory() as td:
            cli = ForegroundAwareFakeCli()
            cli.responses = [
                {"data": {"refs": {"e11": {"name": "Message ChatGPT", "role": "textbox"}}}},
                {"data": {"refs": {"e20": {"name": "Send", "role": "button"}}}},
            ]
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "chrome-use-driver-v3.json",
            )
            self.assertEqual("@e11", asyncio.run(driver._editor_ref("session-visible")))
            self.assertEqual("@e20", asyncio.run(driver._send_ref("session-visible")))
            self.assertEqual(
                ["bringToFront", "snapshot", "bringToFront", "snapshot"],
                [args[0] for _, args, _ in cli.calls],
            )

    def test_submit_clicks_unique_accessibility_send_button_instead_of_pressing_enter(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            conversation = "https://chatgpt.com/c/send-button-test"
            cli.responses = [
                {"success": True},
                {"data": {"value": "https://chatgpt.com/"}},
                {
                    "data": {
                        "refs": {
                            "e10": {"name": "添加文件等", "role": "button"},
                            "e11": {"name": "与 ChatGPT 聊天", "role": "textbox"},
                        },
                        "snapshot": '- textbox "与 ChatGPT 聊天" [ref=e11]',
                    }
                },
                {"success": True},
                {
                    "data": {
                        "refs": {
                            "e11": {"name": "与 ChatGPT 聊天", "role": "textbox"},
                            "e20": {"name": "发送提示词", "role": "button"},
                        },
                        "snapshot": '- textbox "与 ChatGPT 聊天" [ref=e11]: hello\n- button "发送提示词" [ref=e20]',
                    }
                },
                {"success": True},
                {"data": {"value": conversation}},
                {"success": True},
                {"data": {"snapshot": "submitted"}},
            ]
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "chrome-use-driver-v3.json",
                sleeper=lambda _: asyncio.sleep(0),
            )
            asyncio.run(
                driver.submit_prompt(
                    prompt="hello",
                    turn_id="turn-send-button",
                    actor_kind="WORKER",
                    conversation_url=None,
                )
            )
            click_calls = [args for _, args, _ in cli.calls if args and args[0] == "click"]
            press_calls = [args for _, args, _ in cli.calls if args and args[0] == "press"]
            self.assertEqual([["click", "@e20"]], click_calls)
            self.assertEqual([], press_calls)


if __name__ == "__main__":
    unittest.main()
