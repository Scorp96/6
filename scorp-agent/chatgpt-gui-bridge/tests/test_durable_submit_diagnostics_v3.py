import asyncio
import json
import pathlib
import tempfile
import unittest

from durable_actor_transport_v3 import DurableActorTransportV3


class FailingBackend:
    def __init__(self, message):
        self.message = message
        self.submit_calls = 0
        self.recover_calls = 0

    async def submit(self, submission_id, *, prompt, turn_id, actor_kind, conversation_url, timeout_seconds):
        self.submit_calls += 1
        raise RuntimeError(self.message)

    async def recover(self, submission_id, *, turn_id, actor_kind):
        self.recover_calls += 1
        return None

    async def poll(self, handle, *, turn_id, actor_kind, timeout_seconds):
        raise AssertionError("poll must not be called")


class DurableSubmitDiagnosticsV3Tests(unittest.TestCase):
    def test_non_retryable_submit_exception_is_persisted_without_prompt_leakage(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "actor-transport-v3.json"
            secret = "TOP-SECRET-PROMPT-123"
            backend = FailingBackend("CHROME_USE_SEND_REF_COUNT_0 prompt=" + secret)
            transport = DurableActorTransportV3(path, backend)

            with self.assertRaisesRegex(RuntimeError, "CHROME_USE_SEND_REF_COUNT_0"):
                asyncio.run(
                    transport.submit(
                        prompt=secret,
                        turn_id="turn-1",
                        actor_kind="MASTER",
                        conversation_url="https://chatgpt.com/c/a1",
                        timeout_seconds=120,
                    )
                )

            row = json.loads(path.read_text(encoding="utf-8"))["turn-1"]
            self.assertEqual("SUBMITTING", row["state"])
            self.assertEqual("RuntimeError", row["submit_error_type"])
            self.assertIn("CHROME_USE_SEND_REF_COUNT_0", row["submit_error"])
            self.assertNotIn(secret, json.dumps(row))
            self.assertIn("<redacted-prompt>", row["submit_error"])

    def test_recovery_to_ambiguous_preserves_original_submit_diagnostic_without_resubmit(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "actor-transport-v3.json"
            backend = FailingBackend("CHROME_USE_SEND_REF_COUNT_0")
            transport = DurableActorTransportV3(path, backend)
            kwargs = dict(
                prompt="safe diagnostic prompt",
                turn_id="turn-2",
                actor_kind="MASTER",
                conversation_url="https://chatgpt.com/c/a1",
                timeout_seconds=120,
            )

            with self.assertRaises(RuntimeError):
                asyncio.run(transport.submit(**kwargs))
            result = asyncio.run(transport.submit(**kwargs))

            self.assertEqual("AMBIGUOUS", result["status"])
            self.assertEqual(1, backend.submit_calls)
            self.assertEqual(1, backend.recover_calls)
            row = json.loads(path.read_text(encoding="utf-8"))["turn-2"]
            self.assertEqual("RuntimeError", row["submit_error_type"])
            self.assertEqual("CHROME_USE_SEND_REF_COUNT_0", row["submit_error"])


if __name__ == "__main__":
    unittest.main()
