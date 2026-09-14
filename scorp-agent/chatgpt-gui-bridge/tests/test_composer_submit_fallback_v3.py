import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gui_transport as gt


class R:
    def __init__(self, text="ok"):
        self.content = [type("P", (), {"type": "text", "text": text})()]
        self.isError = False


class C:
    def __init__(self, snapshots):
        self.calls = []
        self.snapshots = list(snapshots)

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "Snapshot":
            if not self.snapshots:
                raise AssertionError("UNEXPECTED_EXTRA_SNAPSHOT")
            return R(self.snapshots.pop(0))
        if name == "Clipboard" and args.get("mode") == "get":
            return R("Clipboard content:\nhttps://chatgpt.com/")
        return R("ok")


class ComposerSubmitFallbackV3Tests(unittest.TestCase):
    def test_stale_draft_clear_uses_type_before_chunked_paste(self):
        client = C([
            "Focused Window:\nChatGPT - Google Chrome\nOpened Windows:",
            '(100,200) edit "Message ChatGPT" [value:"STALE DRAFT"]',
            '(900,700) button "Send prompt"',
        ])
        asyncio.run(gt.open_new_chat_and_submit(client, "fresh prompt", page_wait_seconds=0))

        self.assertIn(
            ("Type", {"text": " ", "loc": [100, 200], "clear": True, "press_enter": False}),
            client.calls,
        )
        shortcuts = [args.get("shortcut") for name, args in client.calls if name == "Shortcut"]
        self.assertIn("backspace", shortcuts)
        self.assertIn("ctrl+v", shortcuts)
        self.assertNotIn(("Click", {"loc": [100, 200]}), client.calls)

        prompt_sets = [
            args.get("text")
            for name, args in client.calls
            if name == "Clipboard" and args.get("mode") == "set" and args.get("text") == "fresh prompt"
        ]
        self.assertEqual(prompt_sets, ["fresh prompt"])

    def test_missing_send_control_uses_type_press_enter_fallback(self):
        client = C([
            "Focused Window:\nChatGPT - Google Chrome\nOpened Windows:",
            '(100,200) edit "Message ChatGPT" [value:"STALE DRAFT"]',
            '(100,200) edit "Message ChatGPT" [value:"fresh prompt"]',
        ])
        try:
            asyncio.run(gt.open_new_chat_and_submit(client, "fresh prompt", page_wait_seconds=0))
        except ValueError as exc:
            self.fail(f"composer submit fallback missing: {exc}")

        self.assertIn(
            (
                "Type",
                {
                    "text": " ",
                    "loc": [100, 200],
                    "clear": False,
                    "caret_position": "end",
                    "press_enter": True,
                },
            ),
            client.calls,
        )
        direct_enter = [
            args for name, args in client.calls
            if name == "Shortcut" and args.get("shortcut") == "enter"
        ]
        self.assertEqual(direct_enter, [])

    def test_windows_mcp_sessions_enable_type_tool(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        gui_source = root.joinpath("gui_transport.py").read_text(encoding="utf-8")
        driver_source = root.joinpath("windows_mcp_actor_driver_v3.py").read_text(encoding="utf-8")
        self.assertIn('App,Shortcut,Clipboard,Click,Type,Wait,Snapshot', gui_source)
        self.assertIn('App,Shortcut,Clipboard,Click,Type,Wait,Snapshot', driver_source)


if __name__ == "__main__":
    unittest.main()
