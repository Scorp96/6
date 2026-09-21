import asyncio
import json
import pathlib
import tempfile
import unittest

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from v4_browser_engine import build_v4_browser_engine


class FakeCli:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def run_json(self, session, *args, timeout_seconds=30):
        self.calls.append((session, list(args), timeout_seconds))
        if not self.responses:
            raise AssertionError(f"unexpected call: {session} {args}")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class ChromeUsePersistedRecoveryV3Tests(unittest.TestCase):
    def _seed(self, td, turn_id, url):
        state_path = pathlib.Path(td) / "chrome-use-driver-v3.json"
        driver = ChromeUseActorDriverV3(FakeCli([]), state_path, sleeper=lambda _: asyncio.sleep(0))
        session = driver.bind_turn(turn_id, url)
        return state_path, session

    def test_recovery_reads_exact_persisted_session_without_navigation_or_submit_commands(self):
        with tempfile.TemporaryDirectory() as td:
            turn_id = "turn-recover-ack"
            url = "https://chatgpt.com/c/recover-ack"
            state_path, session = self._seed(td, turn_id, url)
            cli = FakeCli([
                {"data": {"url": url}},
                {"success": True, "data": {"broughtToFront": True}},
                {"data": {"snapshot": "#### ChatGPT said:\nTOKEN-OK"}},
            ])
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))

            text = asyncio.run(driver.recover_persisted_turn_snapshot(turn_id, timeout_seconds=7))

            self.assertIn("TOKEN-OK", text)
            self.assertEqual([session, session, session], [call[0] for call in cli.calls])
            self.assertEqual([["get", "url"], ["bringToFront"], ["read"]], [call[1] for call in cli.calls])
            self.assertTrue(all(call[2] == 7 for call in cli.calls))
            forbidden = {"open", "fill", "click", "press", "type"}
            self.assertFalse(any(args and args[0] in forbidden for _, args, _ in cli.calls))

    def test_recovery_fails_closed_if_persisted_session_is_on_different_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            turn_id = "turn-recover-mismatch"
            url = "https://chatgpt.com/c/expected"
            state_path, session = self._seed(td, turn_id, url)
            cli = FakeCli([{"data": {"url": "https://chatgpt.com/c/other"}}])
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))

            with self.assertRaisesRegex(ValueError, "ACTOR_GUI_RECOVERY_CONVERSATION_MISMATCH"):
                asyncio.run(driver.recover_persisted_turn_snapshot(turn_id, timeout_seconds=5))

            self.assertEqual([(session, ["get", "url"], 5)], cli.calls)

    def test_unpromoted_remote_submit_recovers_same_session_only_when_unique_marker_is_present(self):
        with tempfile.TemporaryDirectory() as td:
            turn_id = "turn-unpromoted"
            token = "SCORP_P0_LIVE_OK::unique::marker"
            url = "https://chatgpt.com/c/unpromoted"
            state_path, session = self._seed(td, turn_id, None)
            cli = FakeCli([
                {"data": {"url": url}},
                {"success": True, "data": {"broughtToFront": True}},
                {
                    "data": {
                        "content": (
                            "#### You said:\n"
                            f"Reply with exactly this one line and nothing else: {token}\n"
                            "#### ChatGPT said:\n"
                            f"{token}\n"
                        ),
                        "url": url,
                    }
                },
            ])
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))

            text = asyncio.run(
                driver.recover_unpromoted_turn_snapshot(
                    turn_id,
                    expected_marker=token,
                    timeout_seconds=6,
                )
            )

            self.assertIn(token, text)
            binding = driver.turn_binding(turn_id)
            self.assertEqual(session, binding["session"])
            self.assertEqual(url, binding["conversation_url"])
            self.assertEqual([["get", "url"], ["bringToFront"], ["read"]], [call[1] for call in cli.calls])
            forbidden = {"open", "fill", "click", "press", "type"}
            self.assertFalse(any(args and args[0] in forbidden for _, args, _ in cli.calls))

    def test_unpromoted_remote_submit_fails_closed_when_unique_marker_is_absent(self):
        with tempfile.TemporaryDirectory() as td:
            turn_id = "turn-unpromoted-missing-marker"
            url = "https://chatgpt.com/c/unpromoted-other"
            state_path, session = self._seed(td, turn_id, None)
            cli = FakeCli([
                {"data": {"url": url}},
                {"success": True, "data": {"broughtToFront": True}},
                {"data": {"content": "#### ChatGPT said:\nunrelated"}},
            ])
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))

            with self.assertRaisesRegex(ValueError, "ACTOR_GUI_RECOVERY_MARKER_MISSING"):
                asyncio.run(
                    driver.recover_unpromoted_turn_snapshot(
                        turn_id,
                        expected_marker="EXPECTED-UNIQUE-MARKER",
                        timeout_seconds=6,
                    )
                )

            binding = driver.turn_binding(turn_id)
            self.assertEqual(session, binding["session"])
            self.assertIsNone(binding["conversation_url"])

    def test_real_driver_master_recovery_uses_short_marker_for_large_prompt(self):
        """Exercise the engine-to-ChromeUse recovery path after a Master crash."""
        with tempfile.TemporaryDirectory() as td:
            turn_id = "master-reasoning-" + "d" * 32
            marker = "SCORP_REASONING::" + "e" * 24
            url = "https://chatgpt.com/c/master-large-prompt"
            state_path, session = self._seed(td, turn_id, None)
            # Model the crash boundary: the turn was durably marked before
            # browser I/O, but URL promotion never completed.
            ChromeUseActorDriverV3(
                FakeCli([]), state_path, sleeper=lambda _: asyncio.sleep(0)
            )._mark_turn_browser_io_started(turn_id)
            long_prompt = "{" + ("durable-state," * 1500) + "}"
            cli = FakeCli([
                {"data": {"url": url}},
                {"success": True, "data": {"broughtToFront": True}},
                {
                    "data": {
                        "content": (
                            "#### You said:\n"
                            + marker + "\n"
                            + long_prompt + "\n"
                            + "#### ChatGPT said:\nMASTER_DECISION\n"
                        ),
                        "url": url,
                    }
                },
            ])
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))

            def parser(snapshot, observed_intent_id):
                if marker not in snapshot or "MASTER_DECISION" not in snapshot:
                    return None
                return {
                    "master_decision_version": 1,
                    "intent_id": observed_intent_id,
                    "action": "WAIT",
                }

            engine = build_v4_browser_engine(
                driver,
                auth_probe=lambda channel: {"status": "AUTHENTICATED"},
                response_parser=parser,
                timeout_seconds=6,
            )
            result = engine.reconcile({
                "intent_id": turn_id,
                "channel": "master",
                "actor_id": "A",
                "action_kind": "MASTER_REASONING",
                "conversation_url": None,
                "payload_json": json.dumps({
                    "prompt": long_prompt,
                    "recovery_marker": marker,
                }),
            })

            self.assertEqual("RESPONSE_CAPTURED", result["status"])
            self.assertEqual(url, result["conversation_url"])
            self.assertEqual(["get", "bringToFront", "read"], [call[1][0] for call in cli.calls])
            forbidden = {"open", "fill", "click", "press", "type"}
            self.assertFalse(any(args and args[0] in forbidden for _, args, _ in cli.calls))
            binding = driver.turn_binding(turn_id)
            self.assertEqual(url, binding["conversation_url"])
            self.assertEqual(session, binding["session"])


if __name__ == "__main__":
    unittest.main()
