import hashlib
import json
import pathlib
import tempfile
import unittest

sys_path = pathlib.Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(sys_path))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from v4_bridge_gateway import QueueConfig, V4BridgeGateway
from gui_engine import ChatGptGuiEngine
from master_a_dynamic_v4.models import CommitResult
from master_a_dynamic_v4.scheduler import SchedulerError


class FakeEngine:
    def __init__(self):
        self.submits = 0
    def auth_state(self, channel):
        return {'status': 'AUTHENTICATED', 'channel': channel}
    def submit(self, intent):
        self.submits += 1
        return {
            'status': 'RESPONSE_CAPTURED',
            'conversation_url': 'https://chatgpt.com/c/v4-gateway',
            'remote_identity': 'turn-v4-gateway',
            'response': {'kind': 'HANDOFF', 'state': 'DONE'},
        }
    def reconcile(self, intent):
        return {'status': 'AMBIGUOUS', 'reason': 'NO_PENDING_REMOTE_PROOF'}


class V4GatewayTests(unittest.TestCase):
    def test_queue_config_rejects_legacy_control_plane_by_default(self):
        with self.assertRaises(ValueError):
            QueueConfig.for_repo('Scorp96/scorp-control-plane')
        self.assertEqual('Scorp96/666', QueueConfig.for_repo().repo)
        self.assertEqual('Scorp96/scorp-control-plane', QueueConfig.for_repo('Scorp96/scorp-control-plane', migration_mode=True).repo)

    def test_gateway_connects_contract_scheduler_and_browser_intent(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            engine = FakeEngine()
            gateway = V4BridgeGateway(root / 'state.sqlite3', 'project-gateway', [worktree], engine)
            try:
                contract = gateway.ensure_contract(
                    {'objective': 'run bounded work'}, {'required': ['AC-GATEWAY']})
                self.assertEqual(64, len(contract['contract_sha256']))
                gateway.enqueue_graph([{
                    'task_id': 'T1', 'objective_sha256': 'a' * 64,
                    'resource_scope': [worktree / 'result.txt'], 'access_mode': 'write',
                    'dependencies': [],
                }])
                claims = gateway.claim_workers()
                self.assertEqual(['T1'], [claim.task_id for claim in claims])
                result_id = gateway.record_worker_result(
                    claims[0], kind='HANDOFF', payload={'result_sha256': 'b' * 64, 'summary': 'done'})
                gateway.verify_worker_result(result_id, result_sha256='b' * 64)
                self.assertEqual('VERIFIED', gateway.scheduler.get_task('T1')['state'])
                intent = gateway.prepare_browser_intent('intent-gateway', 'execute this')
                self.assertEqual('PREPARED', intent['state'])
                result = gateway.submit_intent('intent-gateway')
                self.assertEqual('RESPONSE_CAPTURED', result['state'])
                self.assertEqual(1, engine.submits)
                self.assertEqual('COMPLETED', gateway.store.get_outbox_for_intent('intent-gateway')['state'])
                payload = json.loads(intent['payload_json'])
                self.assertEqual(hashlib.sha256(b'execute this').hexdigest(), payload['prompt_sha256'])
            finally:
                gateway.close()

    def test_gateway_rejects_worker_limit_above_first_release_capacity(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            gateway = V4BridgeGateway(
                root / 'state.sqlite3', 'project-capacity', [worktree], FakeEngine()
            )
            try:
                gateway.ensure_contract(
                    {'objective': 'bounded capacity'}, {'required': ['AC-CAPACITY']}
                )
                before = gateway.describe()
                with self.assertRaisesRegex(SchedulerError, 'V4_WORKER_LIMIT_INVALID'):
                    gateway.claim_workers(limit=3)
                after = gateway.describe()
                self.assertEqual(before['state_version'], after['state_version'])
                self.assertEqual(0, after['pending_intents'])
            finally:
                gateway.close()

    def test_master_facade_fences_epoch_and_commits_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            gateway = V4BridgeGateway(
                root / 'state.sqlite3', 'project-master', [worktree], FakeEngine()
            )
            try:
                gateway.ensure_contract(
                    {'objective': 'master facade'}, {'required': ['AC-MASTER']}
                )
                epoch = gateway.acquire_master_epoch(expected_epoch=0)
                self.assertEqual(1, epoch)
                proposal = {
                    'project_id': 'project-master',
                    'master_identity': 'A',
                    'kind': 'SET_PHASE',
                    'phase': 'PLANNING',
                }
                committed = gateway.commit_master_proposal(
                    'transition-master-1', proposal, master_epoch=epoch, expected_version=0
                )
                self.assertEqual(CommitResult.COMMITTED, committed)
                replay = gateway.commit_master_proposal(
                    'transition-master-1', proposal, master_epoch=epoch, expected_version=0
                )
                self.assertEqual(CommitResult.ALREADY_COMMITTED, replay)
                self.assertEqual('PLANNING', gateway.store.get_project_state('project-master')['phase'])

                next_epoch = gateway.acquire_master_epoch(expected_epoch=epoch)
                self.assertEqual(2, next_epoch)
                stale = gateway.commit_master_proposal(
                    'transition-master-stale', proposal, master_epoch=epoch, expected_version=1
                )
                self.assertEqual(CommitResult.FENCED, stale)
            finally:
                gateway.close()

    def test_gui_engine_requires_explicit_auth_probe_and_bridges_async_turn(self):
        async def run_turn(prompt, request_id, timeout_seconds, conversation_url=None):
            return ({'kind': 'HANDOFF', 'state': 'DONE'}, 'snapshot', 'https://chatgpt.com/c/async')
        engine = ChatGptGuiEngine(
            run_turn,
            auth_probe=lambda channel: {'status': 'AUTHENTICATED', 'channel': channel},
        )
        self.assertEqual('AUTHENTICATED', engine.auth_state('master')['status'])
        result = engine.submit({
            'intent_id': 'intent-async',
            'payload_json': json.dumps({'prompt': 'hello'}),
            'conversation_url': None,
        })
        self.assertEqual('RESPONSE_CAPTURED', result['status'])
        blocked = ChatGptGuiEngine(run_turn, auth_probe=None).auth_state('master')
        self.assertEqual('AUTHENTICATION_UNAVAILABLE', blocked['status'])


if __name__ == '__main__':
    unittest.main()
