import asyncio
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from durable_actor_transport_v3 import DurableActorTransportV3
from gui_transport import ChatGptThrottleError


class RetryableBackend:
    def __init__(self, first_error):
        self.first_error = first_error
        self.submit_calls = 0
        self.recover_calls = 0

    async def submit(self, submission_id, **kwargs):
        self.submit_calls += 1
        if self.submit_calls == 1:
            raise self.first_error
        return {
            "submission_id": submission_id,
            "turn_id": kwargs["turn_id"],
            "actor_kind": kwargs["actor_kind"],
            "conversation_url": "https://chatgpt.com/c/retryable-success",
        }

    async def recover(self, submission_id, **kwargs):
        self.recover_calls += 1
        return None


class UnknownBackend(RetryableBackend):
    pass


class TransportRetryablePreSubmitV3Tests(unittest.TestCase):
    def _submit(self, transport, turn_id="turn-retryable"):
        return asyncio.run(transport.submit(
            prompt="PROMPT",
            turn_id=turn_id,
            actor_kind="WORKER",
            conversation_url=None,
            timeout_seconds=1800,
        ))

    def test_explicit_pre_submit_throttle_becomes_retryable_then_resubmits_without_recover(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "transport.json"
            backend = RetryableBackend(ChatGptThrottleError("CHATGPT_REQUEST_THROTTLED"))
            transport = DurableActorTransportV3(path, backend)
            first = self._submit(transport)
            self.assertEqual("DEFERRED", first["status"])
            self.assertEqual("CHATGPT_REQUEST_THROTTLED", first["reason"])
            row = json.loads(path.read_text(encoding="utf-8"))["turn-retryable"]
            self.assertEqual("RETRYABLE", row["state"])
            second = self._submit(transport)
            self.assertEqual("SUBMITTED", second["status"])
            self.assertEqual(2, backend.submit_calls)
            self.assertEqual(0, backend.recover_calls)

    def test_pre_submit_composer_unavailable_is_retryable(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "transport.json"
            backend = RetryableBackend(RuntimeError("CHATGPT_COMPOSER_UNAVAILABLE"))
            transport = DurableActorTransportV3(path, backend)
            first = self._submit(transport)
            self.assertEqual("DEFERRED", first["status"])
            self.assertEqual("CHATGPT_COMPOSER_UNAVAILABLE", first["reason"])

    def test_unknown_submit_exception_remains_submitting_for_crash_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "transport.json"
            backend = UnknownBackend(RuntimeError("UNKNOWN_GUI_FAILURE"))
            transport = DurableActorTransportV3(path, backend)
            with self.assertRaisesRegex(RuntimeError, "UNKNOWN_GUI_FAILURE"):
                self._submit(transport, "turn-unknown")
            row = json.loads(path.read_text(encoding="utf-8"))["turn-unknown"]
            self.assertEqual("SUBMITTING", row["state"])


if __name__ == "__main__":
    unittest.main()
