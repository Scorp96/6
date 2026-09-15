from __future__ import annotations

import asyncio
import pathlib
import threading
import tempfile
import unittest

from tools.v4_live_two_worker_canary import (
    build_parser,
    cleanup_canary_lifecycle,
    dispatch_intents_concurrently,
    failure_evidence,
    reconcile_intent_until_terminal,
    main,
)


class V4LiveTwoWorkerCanaryTests(unittest.TestCase):
    def test_canary_timeout_covers_five_minute_rate_limit_recovery_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            args = build_parser().parse_args([
                '--database-path', str(root / 'state.sqlite3'),
                '--driver-state-path', str(root / 'driver.json'),
                '--allowed-root', str(root),
                '--evidence-path', str(root / 'evidence.json'),
            ])
        self.assertGreaterEqual(args.timeout_seconds, 360)

    def test_cleanup_closes_auth_and_only_terminal_worker_sessions(self):
        class Store:
            def __init__(self):
                self.states = {
                    "intent-done": {"state": "RESPONSE_CAPTURED"},
                    "intent-ambiguous": {"state": "MAY_HAVE_SUBMITTED"},
                }

            def get_intent(self, intent_id):
                return self.states[intent_id]

        class Driver:
            def __init__(self):
                self.calls = []

            def turn_binding(self, turn_id):
                return {"session": "scorp-p0-turn-" + turn_id}

            async def retire_turn(self, turn_id, **kwargs):
                self.calls.append(("turn", turn_id, kwargs))
                return {"status": "RETIRED", "cleanup": "STOPPED"}

            async def retire_session(self, session, **kwargs):
                self.calls.append(("session", session, kwargs))
                return {"status": "RETIRED", "cleanup": "STOPPED"}

        driver = Driver()
        result = asyncio.run(
            cleanup_canary_lifecycle(
                driver,
                Store(),
                ["intent-done", "intent-ambiguous"],
                auth_session="auth-session",
            )
        )
        self.assertEqual(["intent-done"], result["retired_turn_ids"])
        self.assertEqual("STOPPED", result["auth_session"]["cleanup"])
        self.assertEqual(2, len(driver.calls))
        self.assertEqual("MAY_HAVE_SUBMITTED", result["preserved"][0]["state"])

    def test_reconcile_keeps_other_worker_independent_after_one_failure(self):
        class Store:
            def __init__(self):
                self.confirmed = []

            def get_intent(self, intent_id):
                return {
                    "intent_id": intent_id,
                    "state": "BLOCKED_AMBIGUOUS",
                    "conversation_url": None,
                }

            def confirm_submitted(self, intent_id, **kwargs):
                self.confirmed.append((intent_id, kwargs))

        class Adapter:
            def __init__(self):
                self.calls = []

            def reconcile(self, intent_id):
                self.calls.append(intent_id)
                if intent_id == "intent-1":
                    return {"state": "BLOCKED_AMBIGUOUS", "ambiguity_reason": "BAD_ACK"}
                return {
                    "state": "RESPONSE_CAPTURED",
                    "conversation_url": "https://chatgpt.com/c/intent-2",
                    "response_sha256": "2" * 64,
                }

        class Gateway:
            def __init__(self):
                self.store = Store()
                self.adapter = Adapter()

        class Driver:
            def turn_binding(self, intent_id):
                return {"conversation_url": f"https://chatgpt.com/c/{intent_id}"}

        gateway = Gateway()
        async def run():
            return await asyncio.gather(
                reconcile_intent_until_terminal(
                    gateway,
                    Driver(),
                    {"intent_id": "intent-1"},
                    {"state": "BLOCKED_AMBIGUOUS"},
                    max_attempts=2,
                    sleep_seconds=0,
                ),
                reconcile_intent_until_terminal(
                    gateway,
                    Driver(),
                    {"intent_id": "intent-2"},
                    {"state": "BLOCKED_AMBIGUOUS"},
                    max_attempts=2,
                    sleep_seconds=0,
                ),
            )
        results = asyncio.run(run())
        self.assertEqual("BLOCKED_AMBIGUOUS", results[0]["state"])
        self.assertEqual("RESPONSE_CAPTURED", results[1]["state"])
        self.assertEqual({"intent-1", "intent-2"}, set(gateway.adapter.calls))

    def test_worker_intents_are_submitted_concurrently(self):
        barrier = threading.Barrier(2)

        class Gateway:
            def __init__(self):
                self.calls = []

            def submit_intent(self, intent_id):
                self.calls.append(intent_id)
                barrier.wait(timeout=1)
                return {"intent_id": intent_id, "state": "BLOCKED_AMBIGUOUS"}

        gateway = Gateway()
        results = asyncio.run(
            dispatch_intents_concurrently(gateway, ["intent-1", "intent-2"])
        )
        self.assertEqual({"intent-1", "intent-2"}, set(gateway.calls))
        self.assertEqual(
            ["BLOCKED_AMBIGUOUS", "BLOCKED_AMBIGUOUS"],
            [result["state"] for result in results],
        )

    def test_send_gate_refuses_without_explicit_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            result = main(
                [
                    "--database-path", str(root / "state.sqlite3"),
                    "--driver-state-path", str(root / "driver.json"),
                    "--allowed-root", str(root),
                    "--evidence-path", str(root / "evidence.json"),
                ]
            )
            self.assertEqual(2, result)
            self.assertFalse((root / "evidence.json").exists())

    def test_send_gate_requires_candidate_identity_before_live_canary(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            args = build_parser().parse_args(
                [
                    "--send-canary",
                    "--database-path",
                    str(root / "state.sqlite3"),
                    "--driver-state-path",
                    str(root / "driver.json"),
                    "--allowed-root",
                    str(root),
                    "--evidence-path",
                    str(root / "evidence.json"),
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "CANDIDATE_BINDING_REQUIRED"):
                asyncio.run(
                    __import__("tools.v4_live_two_worker_canary", fromlist=["run_canary"]).run_canary(args)
                )

    def test_failure_evidence_is_blocked_and_records_ambiguous_intent_without_retry(self):
        receipt = failure_evidence(
            project_id="p",
            error=RuntimeError("daemon busy"),
            intents=[
                {"intent_id": "i", "state": "MAY_HAVE_SUBMITTED", "conversation_url": None},
            ],
        )
        self.assertEqual("BLOCKED", receipt["result"])
        self.assertEqual("RuntimeError", receipt["error_type"])
        self.assertEqual(0, receipt["retry_count"])
        self.assertEqual("MAY_HAVE_SUBMITTED", receipt["intents"][0]["state"])


if __name__ == "__main__":
    unittest.main()
