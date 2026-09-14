import asyncio
import pathlib
import tempfile
import unittest

from durable_actor_transport_v3 import DurableActorTransportV3


class FakeBackend:
    def __init__(self):
        self.submit_calls = []
        self.recover_calls = []
        self.poll_calls = []
        self.handles = {}
        self.poll_result = {"status": "PENDING"}
        self.raise_after_effect = False

    async def submit(self, submission_id, *, prompt, turn_id, actor_kind, conversation_url, timeout_seconds):
        self.submit_calls.append(submission_id)
        handle = {
            "submission_id": submission_id,
            "turn_id": turn_id,
            "conversation_url": conversation_url or f"https://chatgpt.com/c/{submission_id[-12:]}",
        }
        self.handles[submission_id] = handle
        if self.raise_after_effect:
            raise RuntimeError("SIMULATED_POST_SUBMIT_CRASH")
        return handle

    async def recover(self, submission_id, *, turn_id, actor_kind):
        self.recover_calls.append(submission_id)
        return self.handles.get(submission_id)

    async def poll(self, handle, *, turn_id, actor_kind, timeout_seconds):
        self.poll_calls.append(handle["submission_id"])
        return dict(self.poll_result)


class DurableActorTransportV3Tests(unittest.TestCase):
    def _transport(self, td, backend):
        return DurableActorTransportV3(pathlib.Path(td) / "actor-transport-v3.json", backend)

    def test_same_turn_is_submitted_exactly_once_and_returns_same_handle(self):
        with tempfile.TemporaryDirectory() as td:
            backend = FakeBackend()
            transport = self._transport(td, backend)
            kwargs = dict(prompt="hello", turn_id="turn-1", actor_kind="WORKER", conversation_url=None, timeout_seconds=120)
            first = asyncio.run(transport.submit(**kwargs))
            second = asyncio.run(transport.submit(**kwargs))
            self.assertEqual("SUBMITTED", first["status"])
            self.assertEqual("ALREADY_SUBMITTED", second["status"])
            self.assertEqual(first["submission_id"], second["submission_id"])
            self.assertEqual(1, len(backend.submit_calls))

    def test_same_turn_changed_binding_fails_closed_without_second_submit(self):
        with tempfile.TemporaryDirectory() as td:
            backend = FakeBackend()
            transport = self._transport(td, backend)
            asyncio.run(transport.submit(prompt="hello", turn_id="turn-1", actor_kind="WORKER", conversation_url=None, timeout_seconds=120))
            with self.assertRaisesRegex(ValueError, "ACTOR_TRANSPORT_BINDING_CONFLICT"):
                asyncio.run(transport.submit(prompt="changed", turn_id="turn-1", actor_kind="WORKER", conversation_url=None, timeout_seconds=120))
            self.assertEqual(1, len(backend.submit_calls))

    def test_crash_after_remote_submit_recovers_without_resubmitting(self):
        with tempfile.TemporaryDirectory() as td:
            backend = FakeBackend()
            backend.raise_after_effect = True
            transport = self._transport(td, backend)
            kwargs = dict(prompt="hello", turn_id="turn-1", actor_kind="WORKER", conversation_url=None, timeout_seconds=120)
            with self.assertRaisesRegex(RuntimeError, "SIMULATED_POST_SUBMIT_CRASH"):
                asyncio.run(transport.submit(**kwargs))
            backend.raise_after_effect = False
            recovered = asyncio.run(transport.submit(**kwargs))
            self.assertEqual("RECOVERED", recovered["status"])
            self.assertEqual(1, len(backend.submit_calls))
            self.assertEqual(1, len(backend.recover_calls))

    def test_unrecoverable_submitting_intent_is_ambiguous_and_never_resubmits(self):
        with tempfile.TemporaryDirectory() as td:
            backend = FakeBackend()
            backend.raise_after_effect = True
            transport = self._transport(td, backend)
            kwargs = dict(prompt="hello", turn_id="turn-1", actor_kind="WORKER", conversation_url=None, timeout_seconds=120)
            with self.assertRaises(RuntimeError):
                asyncio.run(transport.submit(**kwargs))
            backend.handles.clear()
            backend.raise_after_effect = False
            result = asyncio.run(transport.submit(**kwargs))
            self.assertEqual("AMBIGUOUS", result["status"])
            self.assertEqual(1, len(backend.submit_calls))
            self.assertEqual(1, len(backend.recover_calls))

    def test_poll_completion_is_durable_and_not_polled_twice(self):
        with tempfile.TemporaryDirectory() as td:
            backend = FakeBackend()
            transport = self._transport(td, backend)
            submitted = asyncio.run(transport.submit(prompt="hello", turn_id="turn-1", actor_kind="MASTER", conversation_url="https://chatgpt.com/c/a1", timeout_seconds=120))
            backend.poll_result = {
                "status": "COMPLETED",
                "response": {"kind": "WAIT"},
                "snapshot": "done",
                "conversation_url": "https://chatgpt.com/c/a1",
            }
            first = asyncio.run(transport.poll("turn-1", timeout_seconds=120))
            second = asyncio.run(transport.poll("turn-1", timeout_seconds=120))
            self.assertEqual("COMPLETED", first["status"])
            self.assertEqual(first, second)
            self.assertEqual(submitted["submission_id"], first["submission_id"])
            self.assertEqual(1, len(backend.poll_calls))


if __name__ == "__main__":
    unittest.main()
