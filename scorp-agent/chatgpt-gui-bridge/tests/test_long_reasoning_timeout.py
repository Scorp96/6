import asyncio, datetime as dt, inspect, json, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gui_transport as gt
import bridge_worker as bw
from tests.test_bridge_worker import REQ, TASK, FakeGitHub

class LongReasoningTests(unittest.TestCase):
    def test_live_turn_default_timeout_is_at_least_15_minutes(self):
        default = inspect.signature(gt.run_live_turn).parameters['timeout_seconds'].default
        self.assertGreaterEqual(default, 900)

    def test_gui_inflight_lease_covers_long_reasoning_window(self):
        ledger = {}
        persisted = []
        class ProbeGui:
            async def __call__(self, prompt, request_id, conversation_url=None):
                lease = dt.datetime.fromisoformat(ledger[request_id]['lease_until'].replace('Z', '+00:00'))
                remaining = (lease - dt.datetime.now(dt.timezone.utc)).total_seconds()
                if remaining < 1190:
                    raise AssertionError('LEASE_TOO_SHORT')
                raise RuntimeError('PROBE_STOP')
        with self.assertRaisesRegex(RuntimeError, 'PROBE_STOP'):
            asyncio.run(bw.obtain_and_publish_decision(
                REQ, TASK, {'status':'SUCCEEDED'}, FakeGitHub(), ProbeGui(), ledger,
                lambda x: persisted.append(json.loads(json.dumps(x))), lambda: TASK, 'Scorp96'))

if __name__ == '__main__': unittest.main()
