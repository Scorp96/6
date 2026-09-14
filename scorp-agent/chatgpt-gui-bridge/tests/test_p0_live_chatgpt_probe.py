import unittest

from chrome_use_actor_driver_v3 import _render_payload
from p0_live_chatgpt_probe import assistant_reply_matches_token


class P0LiveChatGptProbeTests(unittest.TestCase):
    def test_token_in_user_prompt_only_does_not_pass(self):
        token = "SCORP_P0_LIVE_OK::g-test::abc123"
        conversation = (
            "跳至内容\n\n"
            "#### 你说：\n\n"
            f"Reply with exactly this one line and nothing else: {token}\n\n"
            "#### ChatGPT 说：\n\n"
            "SCORP_P0_LIVE\n\n"
            "ChatGPT 也可能会犯错。请核查重要信息。\n"
        )
        self.assertFalse(assistant_reply_matches_token(conversation, token))

    def test_exact_assistant_reply_passes(self):
        token = "SCORP_P0_LIVE_OK::g-test::abc123"
        conversation = (
            "Skip to content\n\n"
            "#### You said:\n\n"
            f"Reply with exactly this one line and nothing else: {token}\n\n"
            "#### ChatGPT said:\n\n"
            f"{token}\n\n"
            "ChatGPT can make mistakes.\n"
        )
        self.assertTrue(assistant_reply_matches_token(conversation, token))

    def test_partial_assistant_reply_does_not_pass(self):
        token = "SCORP_P0_LIVE_OK::g-test::abc123"
        conversation = (
            "#### 你说：\n\n"
            f"Reply with exactly this one line and nothing else: {token}\n\n"
            "#### ChatGPT 说：\n\n"
            "SCORP_P0_LIVE_OK::g-test\n"
        )
        self.assertFalse(assistant_reply_matches_token(conversation, token))

    def test_nested_read_snapshot_preserves_real_newlines_for_assistant_parser(self):
        token = "SCORP_P0_LIVE_OK::g-test::raw"
        payload = {
            "success": True,
            "data": {
                "snapshot": (
                    "#### You said:\n"
                    f"Reply with exactly this one line and nothing else: {token}\n"
                    "#### ChatGPT said:\n"
                    f"{token}\n"
                )
            },
        }
        rendered = _render_payload(payload)
        self.assertTrue(assistant_reply_matches_token(rendered, token))


if __name__ == "__main__":
    unittest.main()
