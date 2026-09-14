import asyncio
import base64
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from gui_transport import (
    normalize_snapshot_text,
    render_controller_prompt,
    wait_for_terminal_mutation,
    open_new_chat_and_submit,
)

REQ = {
    "request_id": "cont-transport-001",
    "task_id": "task-x",
    "project_id": "proj-x",
    "registry_sequence": 7,
    "continuation_generation": 2,
    "question": "Choose the exact next safe action.",
    "safety_class": "standard",
    "previous_action_id": "action-a",
    "previous_result_sha256": "b" * 64,
}
class FakePart:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResult:
    def __init__(self, text):
        self.content = [FakePart(text)]
        self.isError = False


class FakeClient:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "Snapshot":
            return FakeResult(self.snapshots.pop(0))
        if name == "Clipboard" and args.get("mode") == "get":
            return FakeResult("Clipboard content:\nhttps://chatgpt.com/")
        return FakeResult("ok")
class TransportTests(unittest.TestCase):
    def test_transport_switches_to_chrome_contract(self):
        source = pathlib.Path(__file__).resolve().parents[1].joinpath("gui_transport.py").read_text(encoding="utf-8")
        self.assertIn('call_tool("App"', source)
        self.assertIn('"App,', source)

    def test_submit_clicks_send_button_after_paste(self):
        client = FakeClient([
            'Focused Window:\nChatGPT - Google Chrome\nOpened Windows:',
            '(700,600) edit "Message ChatGPT"',
            '(900,700) button "Send prompt"',
        ])
        asyncio.run(open_new_chat_and_submit(client, "hello", page_wait_seconds=0))
        calls = client.calls
        self.assertIn(("Click", {"loc": [900,700]}), calls)
        submit_shortcuts = [args.get("shortcut") for name,args in calls if name == "Shortcut"]
        self.assertEqual(submit_shortcuts.count("enter"), 0)

    def test_normalize_snapshot_json_list(self):
        raw = '["\\nwindow \\\"ChatGPT\\\"\\ntext \\\"ok\\\""]'
        self.assertIn('window "ChatGPT"', normalize_snapshot_text(raw))

    def test_render_prompt_contains_request_and_constraints(self):
        prompt = render_controller_prompt(REQ, {"status": "SUCCEEDED", "stdout_tail": "A_OK"})
        self.assertIn("cont-transport-001", prompt)
        self.assertIn("SCORP_GUI_MUTATION_V2", prompt)
        self.assertIn("cont-transport-001", prompt)
        self.assertNotIn("SCORP_GUI_MUTATION_V2::cont-transport-001::", prompt)
        self.assertIn("GPT-5.6 Sol", prompt)
        self.assertIn("Do not output binding fields", prompt)

    def test_prompt_does_not_echo_full_response_marker(self):
        prompt = render_controller_prompt(REQ, {"status": "SUCCEEDED"})
        self.assertNotIn("SCORP_GUI_MUTATION_V2::cont-transport-001::", prompt)
        self.assertIn("SCORP_GUI_MUTATION_V2", prompt)
        self.assertIn("cont-transport-001", prompt)

    def test_wait_ignores_streaming_snapshot(self):
        def token(mutation):
            raw = json.dumps(mutation, separators=(",", ":")).encode("utf-8")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        first = token({"kind": "SET_TERMINAL"})
        second = token({"kind": "SET_TERMINAL", "state": "DONE"})
        client = FakeClient([
            f'按钮 "停止回答" text "SCORP_GUI_MUTATION_V2::cont-transport-001::{first}"',
            f'按钮 "启动语音功能" text "SCORP_GUI_MUTATION_V2::cont-transport-001::{second}"',
        ])
        mutation, snap = asyncio.run(wait_for_terminal_mutation(client, "cont-transport-001", timeout_seconds=2, poll_seconds=0))
        self.assertEqual(mutation["kind"], "SET_TERMINAL")
        self.assertIn("启动语音功能", snap)
        self.assertEqual([c[0] for c in client.calls], ["Snapshot", "Snapshot"])


if __name__ == "__main__":
    unittest.main()
