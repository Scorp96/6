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


class ChromeUseConversationReadV3Tests(unittest.TestCase):
    def test_snapshot_conversation_uses_full_read_content_not_truncated_accessibility_name(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            target = "https://chatgpt.com/c/full-read-test"
            token = "SCORP_P0_LIVE_OK::g707::f3a91c7e"
            cli.responses = [
                {"data": {"value": target}},
                {"success": True},
                {
                    "data": {
                        "content": f"ChatGPT said:\n{token}",
                        "finalUrl": target,
                        "url": target,
                    }
                },
            ]
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "driver.json",
                sleeper=lambda _: asyncio.sleep(0),
            )

            text = asyncio.run(driver.snapshot_conversation(target))

            self.assertIn(target, text)
            self.assertIn(token, text)
            self.assertTrue(any(args and args[0] == "read" for _, args, _ in cli.calls))
            self.assertFalse(any(args and args[0] == "snapshot" for _, args, _ in cli.calls))


if __name__ == "__main__":
    unittest.main()
