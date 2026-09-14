import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gui_transport as gt


class Part:
    type = "text"

    def __init__(self, text):
        self.text = text


class Result:
    isError = False

    def __init__(self, text="ok"):
        self.content = [Part(text)]


class Client:
    def __init__(self, snapshots, clipboard_reads):
        self.snapshots = list(snapshots)
        self.clipboard_reads = list(clipboard_reads)
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "Snapshot":
            return Result(self.snapshots.pop(0))
        if name == "Clipboard":
            if args.get("mode") == "get":
                return Result(self.clipboard_reads.pop(0))
            if args.get("mode") == "set":
                return Result("ok")
        if name in {"App", "Wait", "Shortcut"}:
            return Result("ok")
        raise AssertionError(f"UNEXPECTED_TOOL:{name}")


class ChromeAcquisitionTargetBindingV3Tests(unittest.TestCase):
    def test_new_chat_waits_until_focused_address_reaches_root_target(self):
        old = "https://chatgpt.com/c/old-focused-chat"
        root = "https://chatgpt.com/"
        client = Client(
            ["Focused Window:\nChatGPT - Google Chrome\nOpened Windows:"],
            [
                "Clipboard content:\nuser-original",
                f"Clipboard content:\n{old}",
                f"Clipboard content:\n{root}",
            ],
        )
        snapshot = asyncio.run(gt.acquire_chatgpt_window(
            client,
            wait_seconds=0,
            conversation_url=None,
            address_timeout_seconds=1,
            address_poll_seconds=0,
            address_max_attempts=3,
        ))
        self.assertIn("Google Chrome", snapshot)
        self.assertGreaterEqual(
            sum(1 for name, args in client.calls if name == "Shortcut" and args.get("shortcut") == "ctrl+l"),
            2,
        )
        self.assertTrue(any(
            name == "Clipboard"
            and args.get("mode") == "set"
            and args.get("text") == "user-original"
            for name, args in client.calls
        ))

    def test_known_chat_fails_closed_when_focused_address_never_matches_target(self):
        target = "https://chatgpt.com/c/expected-target"
        wrong = "https://chatgpt.com/c/wrong-focused-chat"
        client = Client(
            ["Focused Window:\nChatGPT - Google Chrome\nOpened Windows:"],
            [
                "Clipboard content:\nuser-original",
                f"Clipboard content:\n{wrong}",
                f"Clipboard content:\n{wrong}",
            ],
        )
        with self.assertRaisesRegex(RuntimeError, "CHROME_TARGET_URL_MISMATCH"):
            asyncio.run(gt.acquire_chatgpt_window(
                client,
                wait_seconds=0,
                conversation_url=target,
                address_timeout_seconds=1,
                address_poll_seconds=0,
                address_max_attempts=2,
            ))
        self.assertTrue(any(
            name == "Clipboard"
            and args.get("mode") == "set"
            and args.get("text") == "user-original"
            for name, args in client.calls
        ))


if __name__ == "__main__":
    unittest.main()
