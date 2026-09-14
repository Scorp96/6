import asyncio
import pathlib
import tempfile
import unittest

from actor_gui_backend_v3 import ActorGuiBackendV3
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


def _editor_snapshot():
    return {
        "data": {
            "refs": {
                "e11": {"name": "Message ChatGPT", "role": "textbox"},
            },
            "snapshot": '- textbox "Message ChatGPT" [ref=e11]',
        }
    }


def _send_snapshot(prompt):
    return {
        "data": {
            "refs": {
                "e11": {"name": "Message ChatGPT", "role": "textbox"},
                "e20": {"name": "Send prompt", "role": "button"},
            },
            "snapshot": f'- textbox "Message ChatGPT" [ref=e11]: {prompt}\\n- button "Send prompt" [ref=e20]',
        }
    }


class ChromeUseV3CanonicalReuseCompositionTests(unittest.TestCase):
    def test_existing_canonical_conversation_reuses_one_session_across_backend_turns(self):
        with tempfile.TemporaryDirectory() as td:
            url = "https://chatgpt.com/c/canonical-reuse-composition"
            cli = FakeCli()
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "chrome-use-driver-v3.json",
                sleeper=lambda _: asyncio.sleep(0),
            )
            backend = ActorGuiBackendV3(driver)

            prompts = {
                "turn-alpha": "TURN_ID=turn-alpha\\nalpha",
                "turn-beta": "TURN_ID=turn-beta\\nbeta",
            }
            for turn_id, prompt in prompts.items():
                cli.responses.extend([
                    {"data": {"value": url}},
                    _editor_snapshot(),
                    {"success": True},
                    _send_snapshot(prompt),
                    {"success": True},
                    {"data": {"value": url}},
                    {"success": True, "data": {"broughtToFront": True}},
                    {"data": {"text": "submitted"}},
                ])
                handle = asyncio.run(
                    backend.submit(
                        f"submission-{turn_id}",
                        prompt=prompt,
                        turn_id=turn_id,
                        actor_kind="WORKER",
                        conversation_url=url,
                        timeout_seconds=30,
                    )
                )
                self.assertEqual(url, handle["conversation_url"])

            alpha = driver.turn_binding("turn-alpha")
            beta = driver.turn_binding("turn-beta")
            self.assertIsNotNone(alpha)
            self.assertIsNotNone(beta)
            self.assertEqual(alpha["session"], beta["session"])
            self.assertTrue(alpha["session"].startswith("scorp-p0-conv-"))
            self.assertFalse(alpha["session"].startswith("scorp-p0-turn-"))

            sessions = {session for session, _, _ in cli.calls}
            self.assertEqual({alpha["session"]}, sessions)
            self.assertFalse(any(args and args[0] == "open" for _, args, _ in cli.calls))
            self.assertFalse(any(session.startswith("scorp-p0-turn-") for session, _, _ in cli.calls))


if __name__ == "__main__":
    unittest.main()
