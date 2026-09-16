from __future__ import annotations

import asyncio
import pathlib
import threading
import tempfile
import unittest
from unittest import mock

from tools.v4_live_two_worker_canary import (
    MARKERS,
    _reply_matches,
    build_parser,
    cleanup_canary_lifecycle,
    dispatch_intents_concurrently,
    failure_evidence,
    reconcile_intent_until_terminal,
    main,
)


class V4LiveTwoWorkerCanaryTests(unittest.TestCase):
    def test_canary_markers_are_unambiguous_and_strictly_matched(self):
        for marker in MARKERS.values():
            self.assertRegex(marker, r"^[A-Z0-9]+$")
            self.assertFalse(marker.endswith("_"))
            self.assertTrue(_reply_matches(f"#### ChatGPT said:\n{marker}", marker))
            self.assertFalse(_reply_matches(f"#### ChatGPT said:\n{marker}_", marker))

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

    def test_shared_auth_probe_is_serialized_for_concurrent_workers(self):
        from tools import v4_live_two_worker_canary as module

        active = 0
        max_active = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_probe(cli, driver, session, channel):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            entered.set()
            await release.wait()
            active -= 1
            return {"status": "AUTHENTICATED", "channel": channel}

        class Cli:
            async def run_json(self, *args, **kwargs):
                return {}

        async def exercise():
            probe = module.build_serialized_auth_probe(
                Cli(), object(), "shared-auth-session"
            )
            with mock.patch.object(module, "probe_chatgpt_auth", fake_probe):
                first = asyncio.create_task(probe("worker/worker-slot-1"))
                await entered.wait()
                second = asyncio.create_task(probe("worker/worker-slot-2"))
                await asyncio.sleep(0)
                self.assertEqual(1, max_active)
                self.assertFalse(second.done())
                release.set()
                return await asyncio.gather(first, second)

        results = asyncio.run(exercise())
        self.assertEqual(["worker/worker-slot-1", "worker/worker-slot-2"], [r["channel"] for r in results])

    def test_shared_authenticated_probe_is_cached_after_first_success(self):
        from tools import v4_live_two_worker_canary as module

        class Cli:
            def __init__(self):
                self.opens = 0

            async def run_json(self, *args, **kwargs):
                self.opens += 1
                return {}

        async def fake_probe(cli, driver, session, channel):
            return {"status": "AUTHENTICATED", "channel": channel}

        async def exercise():
            cli = Cli()
            probe = module.build_serialized_auth_probe(cli, object(), "shared-auth-session")
            with mock.patch.object(module, "probe_chatgpt_auth", fake_probe):
                first = await probe("worker/worker-slot-1")
                second = await probe("worker/worker-slot-2")
            return cli.opens, first, second

        opens, first, second = asyncio.run(exercise())
        self.assertEqual(1, opens)
        self.assertEqual("worker/worker-slot-1", first["channel"])
        self.assertEqual("worker/worker-slot-2", second["channel"])

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
