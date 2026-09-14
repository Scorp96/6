import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import p0_live_chatgpt_probe as probe
from p0_live_chatgpt_probe import recover_persisted_live_ack


class FakeDriver:
    def __init__(self, *, snapshot, binding=None, delay=0):
        self.snapshot = snapshot
        self.binding = (
            {
                "session": "scorp-p0-turn-test",
                "conversation_url": "https://chatgpt.com/c/test-live-ack",
            }
            if binding is None
            else binding
        )
        self.delay = delay
        self.recovery_calls = []
        self.unpromoted_calls = []
        self.submit_calls = 0

    def turn_binding(self, turn_id):
        return dict(self.binding)

    async def recover_persisted_turn_snapshot(self, turn_id, timeout_seconds=None):
        self.recovery_calls.append((turn_id, timeout_seconds))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.snapshot

    async def recover_unpromoted_turn_snapshot(self, turn_id, expected_marker, timeout_seconds=None):
        self.unpromoted_calls.append((turn_id, expected_marker, timeout_seconds))
        if self.delay:
            await asyncio.sleep(self.delay)
        self.binding["conversation_url"] = "https://chatgpt.com/c/recovered-unpromoted"
        return self.snapshot

    async def submit_prompt(self, **kwargs):
        self.submit_calls += 1
        raise AssertionError("recovery must never submit")


class P0LiveAckRecoveryTests(unittest.TestCase):
    def test_exact_assistant_ack_is_verified_without_submit(self):
        token = "SCORP_P0_LIVE_OK::fresh::abc123"
        snapshot = (
            "#### You said:\n"
            f"Reply with exactly this one line and nothing else: {token}\n"
            "#### ChatGPT said:\n"
            f"{token}\n"
        )
        driver = FakeDriver(snapshot=snapshot)

        result = asyncio.run(
            recover_persisted_live_ack(
                driver=driver,
                turn_id="fresh-turn",
                token=token,
                command_timeout_seconds=4,
                transaction_timeout_seconds=10,
            )
        )

        self.assertEqual("VERIFIED", result["status"])
        self.assertEqual("scorp-p0-turn-test", result["session"])
        self.assertEqual("https://chatgpt.com/c/test-live-ack", result["conversation_url"])
        self.assertEqual([("fresh-turn", 4)], driver.recovery_calls)
        self.assertEqual([], driver.unpromoted_calls)
        self.assertEqual(0, driver.submit_calls)

    def test_token_only_in_user_prompt_is_not_verified(self):
        token = "SCORP_P0_LIVE_OK::fresh::abc123"
        snapshot = (
            "#### You said:\n"
            f"Reply with exactly this one line and nothing else: {token}\n"
            "#### ChatGPT said:\n"
            "partial response\n"
        )
        driver = FakeDriver(snapshot=snapshot)

        result = asyncio.run(
            recover_persisted_live_ack(
                driver=driver,
                turn_id="fresh-turn",
                token=token,
                command_timeout_seconds=4,
                transaction_timeout_seconds=10,
            )
        )

        self.assertEqual("ACK_NOT_VERIFIED", result["status"])
        self.assertEqual(0, driver.submit_calls)

    def test_transaction_timeout_is_bounded_and_never_submits(self):
        driver = FakeDriver(snapshot="", delay=0.2)

        with self.assertRaisesRegex(TimeoutError, "LIVE_ACK_RECOVERY_TIMEOUT"):
            asyncio.run(
                recover_persisted_live_ack(
                    driver=driver,
                    turn_id="fresh-turn",
                    token="TOKEN",
                    command_timeout_seconds=1,
                    transaction_timeout_seconds=0.01,
                )
            )

        self.assertEqual(0, driver.submit_calls)

    def test_missing_persisted_binding_fails_closed_before_read(self):
        driver = FakeDriver(snapshot="", binding={})

        with self.assertRaisesRegex(ValueError, "LIVE_ACK_RECOVERY_BINDING_MISSING"):
            asyncio.run(
                recover_persisted_live_ack(
                    driver=driver,
                    turn_id="fresh-turn",
                    token="TOKEN",
                    command_timeout_seconds=1,
                    transaction_timeout_seconds=1,
                )
            )

        self.assertEqual([], driver.recovery_calls)
        self.assertEqual([], driver.unpromoted_calls)
        self.assertEqual(0, driver.submit_calls)

    def test_unpromoted_remote_submit_recovers_and_verifies_without_resubmit(self):
        token = "SCORP_P0_LIVE_OK::fresh::unpromoted"
        snapshot = (
            "#### You said:\n"
            f"Reply with exactly this one line and nothing else: {token}\n"
            "#### ChatGPT said:\n"
            f"{token}\n"
        )
        driver = FakeDriver(
            snapshot=snapshot,
            binding={"session": "scorp-p0-turn-unpromoted", "conversation_url": None},
        )

        result = asyncio.run(
            recover_persisted_live_ack(
                driver=driver,
                turn_id="unpromoted-turn",
                token=token,
                command_timeout_seconds=5,
                transaction_timeout_seconds=12,
            )
        )

        self.assertEqual("VERIFIED", result["status"])
        self.assertEqual("https://chatgpt.com/c/recovered-unpromoted", result["conversation_url"])
        self.assertEqual([], driver.recovery_calls)
        self.assertEqual([("unpromoted-turn", token, 5)], driver.unpromoted_calls)
        self.assertEqual(0, driver.submit_calls)

    def test_live_probe_submits_once_then_uses_persisted_recovery(self):
        token = "SCORP_P0_LIVE_OK::fresh::wired"

        class OneSubmitDriver:
            def __init__(self, *args, **kwargs):
                self.submit_calls = 0

            async def submit_prompt(self, **kwargs):
                self.submit_calls += 1
                return "#### ChatGPT said:\npartial"

            def turn_binding(self, turn_id):
                return {
                    "session": "scorp-p0-turn-wired",
                    "conversation_url": "https://chatgpt.com/c/wired",
                }

        driver = OneSubmitDriver()
        args = SimpleNamespace(
            executable="chrome-use.exe",
            state_path="unused.json",
            turn_id="fresh-wired",
            token=token,
            timeout_seconds=60,
            poll_seconds=15,
            recovery_command_timeout_seconds=4,
            recovery_transaction_timeout_seconds=10,
        )
        recovered = {
            "status": "VERIFIED",
            "turn_id": "fresh-wired",
            "session": "scorp-p0-turn-wired",
            "conversation_url": "https://chatgpt.com/c/wired",
        }

        with mock.patch.object(probe, "ChromeUseCliV3"), \
             mock.patch.object(probe, "ChromeUseActorDriverV3", return_value=driver), \
             mock.patch.object(probe, "recover_persisted_live_ack", new=mock.AsyncMock(return_value=recovered)) as recover_mock, \
             mock.patch.object(probe.asyncio, "sleep", new=mock.AsyncMock()):
            rc = asyncio.run(probe.run(args))

        self.assertEqual(0, rc)
        self.assertEqual(1, driver.submit_calls)
        recover_mock.assert_awaited_once_with(
            driver=driver,
            turn_id="fresh-wired",
            token=token,
            command_timeout_seconds=4,
            transaction_timeout_seconds=10,
        )


if __name__ == "__main__":
    unittest.main()
