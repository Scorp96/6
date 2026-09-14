import asyncio
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from master_worker_relay_v3 import _sha
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from session_registry_v3 import SessionRegistryV3

UTC = dt.timezone.utc


class PendingTransport:
    def __init__(self):
        self.poll_calls = []
        self.timeout_calls = []

    async def submit(self, **kwargs):
        raise AssertionError("ADAPTIVE_POLL_TEST_MUST_NOT_SUBMIT")

    async def poll(self, turn_id, *, timeout_seconds=1800):
        self.poll_calls.append(turn_id)
        return {"status": "PENDING", "turn_id": turn_id, "submission_id": f"submit-{turn_id}"}

    def mark_timed_out(self, turn_id, *, reason, timed_out_at):
        self.timeout_calls.append((turn_id, reason, timed_out_at))
        return {"status": "TIMED_OUT", "turn_id": turn_id, "submission_id": f"submit-{turn_id}"}


class AdaptiveActorPollV3Tests(unittest.TestCase):
    def test_generic_relay_defaults_to_immediate_polling(self):
        with tempfile.TemporaryDirectory() as td:
            relay = ParallelMasterWorkerRelayV3(
                pathlib.Path(td),
                SessionRegistryV3(pathlib.Path(td) / "sessions.json"),
                PendingTransport(),
            )
            self.assertFalse(relay.adaptive_browser_poll)

    def _turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-adaptive-poll",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "WORKER",
            "actor_id": "worker-adaptive",
            "session_id": "worker-adaptive-session",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {
                "event": "ASSIGNMENT",
                "assignment": {"objective_sha256": "1" * 64, "resource_scope": ["adaptive-poll"]},
            },
        }

    def _relay(self, root, transport):
        return ParallelMasterWorkerRelayV3(
            root,
            SessionRegistryV3(pathlib.Path(root) / "sessions.json"),
            transport,
            max_inflight=5,
            max_workers=4,
            adaptive_browser_poll=True,
        )

    def _seed(self, root, relay, turn, submitted_at):
        relay._write_child(turn)
        row = {
            "state": "GUI_SUBMITTED",
            "envelope_sha256": _sha(turn),
            "actor_kind": turn["actor_kind"],
            "actor_id": turn["actor_id"],
            "session_id": turn["session_id"],
            "submission_id": f"submit-{turn['turn_id']}",
            "conversation_url": f"https://chatgpt.com/c/{turn['turn_id']}",
            "submitted_at": submitted_at,
        }
        (pathlib.Path(root) / "master-worker-v3-ledger.json").write_text(
            json.dumps({turn["turn_id"]: row}, indent=2) + "\n", encoding="utf-8"
        )

    def test_browser_poll_uses_persistent_60_120_300_second_backoff_and_final_timeout_poll(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            transport = PendingTransport()
            relay = self._relay(root, transport)
            turn = self._turn()
            started = dt.datetime(2026, 9, 13, 4, 0, tzinfo=UTC)
            self._seed(root, relay, turn, started.isoformat().replace("+00:00", "Z"))

            observed = []
            previous = 0
            for elapsed in range(0, 1801, 10):
                asyncio.run(relay.run_tick(now=started + dt.timedelta(seconds=elapsed)))
                if len(transport.poll_calls) != previous:
                    observed.append(elapsed)
                    previous = len(transport.poll_calls)

            self.assertEqual(
                [60, 120, 180, 240, 300, 420, 540, 660, 780, 900, 1200, 1500, 1800],
                observed,
            )
            self.assertEqual(13, len(transport.poll_calls))
            self.assertEqual(1, len(transport.timeout_calls))
            ledger = json.loads((root / "master-worker-v3-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual("ROUTED", ledger[turn["turn_id"]]["state"])

    def test_poll_schedule_survives_relay_restart_without_early_browser_read(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            turn = self._turn()
            started = dt.datetime(2026, 9, 13, 4, 0, tzinfo=UTC)
            first_transport = PendingTransport()
            first = self._relay(root, first_transport)
            self._seed(root, first, turn, started.isoformat().replace("+00:00", "Z"))

            asyncio.run(first.run_tick(now=started + dt.timedelta(seconds=60)))
            self.assertEqual(1, len(first_transport.poll_calls))

            second_transport = PendingTransport()
            second = self._relay(root, second_transport)
            asyncio.run(second.run_tick(now=started + dt.timedelta(seconds=70)))
            asyncio.run(second.run_tick(now=started + dt.timedelta(seconds=119)))
            self.assertEqual([], second_transport.poll_calls)
            asyncio.run(second.run_tick(now=started + dt.timedelta(seconds=120)))
            self.assertEqual([turn["turn_id"]], second_transport.poll_calls)


if __name__ == "__main__":
    unittest.main()
