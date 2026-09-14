import asyncio
import pathlib
import tempfile
import unittest

from actor_gui_backend_v3 import ActorGuiBackendV3
from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from durable_actor_transport_v3 import DurableActorTransportV3


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
            "refs": {"e11": {"name": "Message ChatGPT", "role": "textbox"}},
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


def _queue_submit(cli, url, prompt):
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


class DurableChromeUseCompositionV3Tests(unittest.TestCase):
    def test_durable_transport_preserves_exactly_once_and_reuses_canonical_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            url = "https://chatgpt.com/c/durable-canonical-reuse"
            cli = FakeCli()
            driver = ChromeUseActorDriverV3(
                cli,
                root / "chrome-use-driver-v3.json",
                sleeper=lambda _: asyncio.sleep(0),
            )
            backend = ActorGuiBackendV3(driver)
            transport = DurableActorTransportV3(root / "actor-ledger-v3.json", backend)

            prompt_a = "TURN_ID=turn-a\\nalpha"
            _queue_submit(cli, url, prompt_a)
            first = asyncio.run(
                transport.submit(
                    prompt=prompt_a,
                    turn_id="turn-a",
                    actor_kind="WORKER",
                    conversation_url=url,
                    timeout_seconds=30,
                )
            )
            self.assertEqual("SUBMITTED", first["status"])
            calls_after_first = len(cli.calls)

            duplicate = asyncio.run(
                transport.submit(
                    prompt=prompt_a,
                    turn_id="turn-a",
                    actor_kind="WORKER",
                    conversation_url=url,
                    timeout_seconds=30,
                )
            )
            self.assertEqual("ALREADY_SUBMITTED", duplicate["status"])
            self.assertEqual(calls_after_first, len(cli.calls))

            prompt_b = "TURN_ID=turn-b\\nbeta"
            _queue_submit(cli, url, prompt_b)
            second = asyncio.run(
                transport.submit(
                    prompt=prompt_b,
                    turn_id="turn-b",
                    actor_kind="WORKER",
                    conversation_url=url,
                    timeout_seconds=30,
                )
            )
            self.assertEqual("SUBMITTED", second["status"])

            a = driver.turn_binding("turn-a")
            b = driver.turn_binding("turn-b")
            self.assertEqual(a["session"], b["session"])
            self.assertTrue(a["session"].startswith("scorp-p0-conv-"))

            sessions = {session for session, _, _ in cli.calls}
            self.assertEqual({a["session"]}, sessions)
            self.assertFalse(any(args and args[0] == "open" for _, args, _ in cli.calls))
            self.assertFalse(any(session.startswith("scorp-p0-turn-") for session, _, _ in cli.calls))


if __name__ == "__main__":
    unittest.main()
