import asyncio
import datetime as dt
import pathlib
import tempfile
import unittest

from master_worker_coordinator_v3 import MasterWorkerCoordinatorV3

UTC = dt.timezone.utc


class FakeStateStore:
    def load(self):
        return {
            "project_id": "parallel-service-project",
            "status": "ACTIVE",
            "state_version": 7,
            "goal_contract_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
        }


class FakeLeaseStore:
    pass


class FakeWatchdog:
    def __init__(self):
        self.calls = []

    def run_once(self, now=None):
        self.calls.append(now)
        return {
            "status": "MASTER_ACTIVE",
            "project_id": "parallel-service-project",
            "state_version": 7,
            "session_id": "a-window-v7-live",
        }


class FakePump:
    def __init__(self):
        self.calls = []

    def run_once(self, project_id, now=None):
        self.calls.append((project_id, now))
        return {"status": "IDLE", "project_id": project_id}


class ParallelOnlyRelay:
    def __init__(self):
        self.calls = []

    async def run_tick(self, now=None):
        self.calls.append(now)
        return {
            "status": "ACTIVE",
            "submitted": 2,
            "completed": 1,
            "recovered": 0,
            "ambiguous": 0,
            "inflight": 3,
        }


class MasterWorkerCoordinatorParallelV3Tests(unittest.TestCase):
    def test_coordinator_uses_parallel_run_tick_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            now = dt.datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
            watchdog = FakeWatchdog()
            pump = FakePump()
            relay = ParallelOnlyRelay()
            coordinator = MasterWorkerCoordinatorV3(
                "parallel-service-project",
                FakeStateStore(),
                FakeLeaseStore(),
                watchdog,
                pump,
                relay,
                pathlib.Path(td) / "outbox",
            )

            result = asyncio.run(coordinator.run_once(now=now))

            self.assertEqual("TICK", result["status"])
            self.assertEqual("ACTIVE", result["relay"]["status"])
            self.assertEqual(3, result["relay"]["inflight"])
            self.assertEqual([now], relay.calls)
            self.assertEqual([now], watchdog.calls)
            self.assertEqual([("parallel-service-project", now)], pump.calls)


if __name__ == "__main__":
    unittest.main()
