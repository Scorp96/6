import asyncio
import unittest
from unittest.mock import AsyncMock

import gui_transport


class Result:
    isError = False

    def __init__(self, text="ok"):
        self.content = [type("Part", (), {"type": "text", "text": text})()]


class ConversationReuseTests(unittest.TestCase):
    def test_existing_conversation_is_launched_instead_of_new_chat(self):
        target = "https://chatgpt.com/c/abc-123"
        client = AsyncMock()

        async def call_tool(name, args):
            if name == "Snapshot":
                return Result("Focused Window:\nChatGPT - Google Chrome\nOpened Windows:")
            if name == "Clipboard" and args.get("mode") == "get":
                return Result("Clipboard content:\n" + target)
            return Result()

        client.call_tool.side_effect = call_tool
        asyncio.run(gui_transport.acquire_chatgpt_window(client, 0, target))
        app = client.call_tool.await_args_list[0]
        self.assertEqual(app.args[1]["args"], ["--new-window", target])

    def test_invalid_saved_conversation_fails_before_launch(self):
        client = AsyncMock()
        with self.assertRaisesRegex(ValueError, "CONVERSATION_URL_INVALID"):
            asyncio.run(gui_transport.acquire_chatgpt_window(
                client, 0, "https://evil.example/c/abc"))
        client.call_tool.assert_not_awaited()
