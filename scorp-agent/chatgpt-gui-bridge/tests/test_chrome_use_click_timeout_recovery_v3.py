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


class ChromeUseClickTimeoutRecoveryV3Tests(unittest.TestCase):
    def test_click_timeout_recovers_canonical_url_without_second_submit(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / "driver.json"
            cli = FakeCli()
            conversation = "https://chatgpt.com/c/recovered-after-click-timeout"
            cli.responses = [
                {"success": True},
                {"data": {"value": "https://chatgpt.com/"}},
                {
                    "data": {
                        "refs": {"e11": {"name": "Message ChatGPT", "role": "textbox"}},
                        "snapshot": '- textbox "Message ChatGPT" [ref=e11]',
                    }
                },
                {"success": True},
                {
                    "data": {
                        "refs": {
                            "e11": {"name": "Message ChatGPT", "role": "textbox"},
                            "e20": {"name": "Send prompt", "role": "button"},
                        },
                        "snapshot": '- textbox "Message ChatGPT" [ref=e11]: hello\n- button "Send prompt" [ref=e20]',
                    }
                },
                TimeoutError("CHROME_USE_TIMEOUT"),
                {"data": {"value": conversation}},
                {"success": True},
                {"data": {"content": "submitted remotely"}},
            ]
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))

            snapshot = asyncio.run(
                driver.submit_prompt(
                    prompt="hello",
                    turn_id="turn-click-timeout",
                    actor_kind="WORKER",
                    conversation_url=None,
                )
            )

            self.assertIn("submitted remotely", snapshot)
            binding = driver.turn_binding("turn-click-timeout")
            self.assertEqual(conversation, binding["conversation_url"])
            click_calls = [args for _, args, _ in cli.calls if args and args[0] == "click"]
            fill_calls = [args for _, args, _ in cli.calls if args and args[0] == "fill"]
            self.assertEqual([["fill", "@e11", "hello"]], fill_calls)
            self.assertEqual([["click", "@e20"]], click_calls)

    def test_click_timeout_retries_readonly_url_recovery_without_second_submit(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / "driver.json"
            cli = FakeCli()
            conversation = "https://chatgpt.com/c/recovered-after-transient-get-url-timeout"
            cli.responses = [
                {"success": True},
                {"data": {"value": "https://chatgpt.com/"}},
                {
                    "data": {
                        "refs": {"e11": {"name": "Message ChatGPT", "role": "textbox"}},
                        "snapshot": '- textbox "Message ChatGPT" [ref=e11]',
                    }
                },
                {"success": True},
                {
                    "data": {
                        "refs": {
                            "e11": {"name": "Message ChatGPT", "role": "textbox"},
                            "e20": {"name": "Send prompt", "role": "button"},
                        },
                        "snapshot": '- textbox "Message ChatGPT" [ref=e11]: hello\n- button "Send prompt" [ref=e20]',
                    }
                },
                TimeoutError("CHROME_USE_TIMEOUT"),
                TimeoutError("CHROME_USE_TIMEOUT"),
                {"data": {"value": conversation}},
                {"success": True},
                {"data": {"content": "submitted remotely after delayed readonly recovery"}},
            ]
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))

            snapshot = asyncio.run(
                driver.submit_prompt(
                    prompt="hello",
                    turn_id="turn-click-timeout-delayed-recovery",
                    actor_kind="WORKER",
                    conversation_url=None,
                )
            )

            self.assertIn("submitted remotely after delayed readonly recovery", snapshot)
            binding = driver.turn_binding("turn-click-timeout-delayed-recovery")
            self.assertEqual(conversation, binding["conversation_url"])
            click_calls = [args for _, args, _ in cli.calls if args and args[0] == "click"]
            get_url_calls = [args for _, args, _ in cli.calls if args[:2] == ["get", "url"]]
            self.assertEqual([["click", "@e20"]], click_calls)
            self.assertEqual(3, len(get_url_calls))


if __name__ == "__main__":
    unittest.main()
