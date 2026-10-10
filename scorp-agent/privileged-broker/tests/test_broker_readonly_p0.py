"""P0 mainline Broker authorization — isolated fake backend, no SYSTEM operations."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from broker_client import build_signed_request
from broker_service import BrokerHandler, _READ_ONLY_OPERATIONS


NOW = dt.datetime(2026, 10, 10, 0, 0, tzinfo=dt.timezone.utc)
SECRET = b"k" * 32


class FakeBackend:
    def __init__(self):
        self.calls = []

    def identity_get(self):
        self.calls.append("identity.get")
        return {"identity": "isolated-test"}

    def service_get(self, name):
        self.calls.append("service.get")
        return {"name": name, "status": "Running"}

    def task_get(self, name):
        self.calls.append("task.get")
        return {"name": name, "state": "Ready"}

    def __getattr__(self, name):
        raise AssertionError("UNAUTHORIZED_BACKEND_CALL:" + name)


class P0ReadOnlyBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.backend = FakeBackend()
        self.handler = BrokerHandler(
            SECRET, self.dir / "ledger.json", self.dir / "audit.jsonl", self.backend
        )

    def make(self, operation, params=None, reqid=None):
        return build_signed_request(
            operation, params or {}, SECRET,
            request_id=reqid or ("p0-" + operation), now=NOW,
        )

    def test_approved_capabilities_are_an_explicit_positive_set(self):
        self.assertEqual(
            frozenset({"identity.get", "service.get", "task.get"}),
            _READ_ONLY_OPERATIONS,
        )

    def test_all_four_existing_mutations_rejected_before_ledger_or_dispatch(self):
        operations = {
            "service.restart": {"name": "ScorpWorker"},
            "task.run": {"name": "ScorpComputerAgent"},
            "file.write": {"path": "proof.txt", "text": "danger"},
            "registry.set": {
                "path": "HKLM\\Software\\ScorpAgent",
                "name": "Policy", "value": "1", "value_type": "string",
            },
        }
        for operation, params in operations.items():
            with self.subTest(operation=operation):
                response = self.handler.handle(self.make(operation, params), now=NOW)
                self.assertEqual(response["status"], "ERROR")
                self.assertEqual(
                    response["error"], "BROKER_MUTATION_DISABLED_PENDING_TASK_APPROVAL",
                )
        self.assertFalse((self.dir / "ledger.json").exists())
        self.assertEqual([], self.backend.calls)

    def test_new_future_operation_never_enters_validator_or_dispatch(self):
        request = self.make("future.any.privileged", {"name": "ScorpAny"})
        with patch("broker_service.validate_operation") as validate, patch(
            "broker_service.dispatch_operation"
        ) as dispatch:
            response = self.handler.handle(request, now=NOW)
            validate.assert_not_called()
            dispatch.assert_not_called()
        self.assertEqual("ERROR", response["status"])
        self.assertEqual(
            "BROKER_MUTATION_DISABLED_PENDING_TASK_APPROVAL", response["error"]
        )
        self.assertFalse((self.dir / "ledger.json").exists())

    def test_read_only_queries_execute_under_exact_capability(self):
        for name, op, params in (
            ("identity.get", "identity.get", {}),
            ("service.get", "service.get", {"name": "Spooler"}),
            ("task.get", "task.get", {"name": "ScorpComputerAgent"}),
        ):
            with self.subTest(operation=name):
                reply = self.handler.handle(self.make(op, params), now=NOW)
                self.assertEqual("OK", reply["status"])
                self.assertFalse(reply["replayed"])
        self.assertEqual(
            ["identity.get", "service.get", "task.get"], self.backend.calls
        )

    def test_legacy_completed_mutation_can_only_replay_original_receipt(self):
        request = self.make("service.restart", {"name": "ScorpWorker"}, "legacy-done")
        self.handler.ledger.mark_inflight(request)
        receipt = {"name": "ScorpWorker", "status": "Running"}
        self.handler.ledger.complete(request, receipt)
        reply = self.handler.handle(request, now=NOW + dt.timedelta(days=1))
        self.assertEqual("OK", reply["status"])
        self.assertTrue(reply["replayed"])
        self.assertEqual(receipt, reply["result"])
        self.assertEqual([], self.backend.calls)

    def test_legacy_inflight_mutation_is_not_retried(self):
        request = self.make("task.run", {"name": "ScorpComputerAgent"}, "legacy-inflight")
        self.handler.ledger.mark_inflight(request)
        reply = self.handler.handle(request, now=NOW)
        self.assertEqual("ERROR", reply["status"])
        self.assertEqual("REQUEST_INFLIGHT", reply["error"])
        self.assertEqual([], self.backend.calls)


if __name__ == "__main__":
    unittest.main()
