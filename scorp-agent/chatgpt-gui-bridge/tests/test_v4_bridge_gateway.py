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
        self.worker_intents = []
    def auth_state(self, channel):
        return {'status': 'AUTHENTICATED', 'channel': channel}
    def submit(self, intent):
        self.submits += 1
        self.worker_intents.append(dict(intent))
        return {
            'status': 'RESPONSE_CAPTURED',
            'conversation_url': 'https://chatgpt.com/c/v4-gateway',
            'remote_identity': 'turn-v4-gateway',
            'response': {'kind': 'HANDOFF', 'state': 'DONE'},
        }
    def reconcile(self, intent):
        return {'status': 'AMBIGUOUS', 'reason': 'NO_PENDING_REMOTE_PROOF'}


class StructuredWorkerEngine(FakeEngine):
    def submit(self, intent):
        self.submits += 1
        assignment = intent['payload']['worker_assignment']
        result = {
            'work_result_version': '1',
            'project_id': intent['project_id'],
            'worker_id': assignment['worker_id'],
            'assignment_id': assignment['assignment_id'],
            'task_id': assignment['task_id'],
            'objective_sha256': assignment['objective_sha256'],
            'base_state_version': assignment['base_state_version'],
            'candidate_commit': 'a' * 40,
            'status': 'COMPLETE',
            'scope_completed': [assignment['task_id']],
            'scope_not_completed': [],
            'deliverables': [{'path': assignment['task_id'] + '.txt'}],
            'evidence': [{'kind': 'structured'}],
            'acceptance_coverage': ['AC-CONTROLLER'],
            'facts': ['worker completed bounded assignment'],
            'inferences': [],
            'unknowns': [],
            'contradictions': [],
            'followup_proposals': [],
        }
        from master_a_dynamic_v4.work_result import result_content_sha256
        result['result_sha256'] = result_content_sha256(result)
        self.worker_intents.append(dict(intent))
        return {
            'status': 'RESPONSE_CAPTURED',
            'conversation_url': 'https://chatgpt.com/c/v4-controller',
            'remote_identity': 'turn-v4-controller',
            'response': result,
        }


class V4GatewayTests(unittest.TestCase):
    def test_master_controller_runs_real_gateway_two_worker_structured_loop(self):
        from master_a_dynamic_v4.master_controller import MasterAController

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            gateway = V4BridgeGateway(
                root / 'state.sqlite3', 'project-controller', [worktree], StructuredWorkerEngine()
            )
            try:
                controller = MasterAController(gateway, 'master-controller')
                controller.start(
                    {'objective': 'run two structured workers'},
                    {'required': ['AC-CONTROLLER']},
                )
                controller.apply_plan({
                    'project_id': 'project-controller',
                    'master_identity': 'A',
                    'tasks': [
                        {'task_id': 'T1', 'objective_sha256': '1' * 64, 'resource_scope': [worktree / 'a.txt'], 'dependencies': [], 'acceptance_criteria_ids': ['AC-CONTROLLER']},
                        {'task_id': 'T2', 'objective_sha256': '2' * 64, 'resource_scope': [worktree / 'b.txt'], 'dependencies': [], 'acceptance_criteria_ids': ['AC-CONTROLLER']},
                        {'task_id': 'T3', 'objective_sha256': '3' * 64, 'resource_scope': [worktree / 'report.json'], 'dependencies': ['T1', 'T2'], 'acceptance_criteria_ids': ['AC-CONTROLLER']},
                    ],
                })
                history = controller.run_cycles(
                    lambda claim: 'return WORK_RESULT/1 for ' + claim.task_id,
                    lambda row: json.loads(row['response_json']),
                    max_cycles=4,
                )
                self.assertEqual(['DISPATCHED', 'DISPATCHED', 'IDLE'], [item.status for item in history])
                self.assertEqual(2, len(history[0].outcomes))
                self.assertEqual({'T1', 'T2'}, {item['task_id'] for item in history[0].outcomes})
                self.assertEqual(['T3'], [item['task_id'] for item in history[1].outcomes])
                self.assertEqual({'ACCEPTED'}, {gateway.scheduler.get_task(task)['state'] for task in ('T1', 'T2', 'T3')})
            finally:
                gateway.close()

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

    def test_master_session_watchdog_is_exposed_through_gateway(self):
        import datetime as dt

        start = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            gateway = V4BridgeGateway(
                root / 'state.sqlite3', 'project-session', [worktree], FakeEngine(), master_ttl_seconds=30
            )
            try:
                gateway.ensure_contract({'objective': 'session'}, {'required': ['AC-SESSION']})
                started = gateway.start_master_session('master-1', now=start)
                self.assertEqual('ACTIVE', started['state'])
                self.assertEqual('MASTER_ACTIVE', gateway.watchdog_once(now=start + dt.timedelta(seconds=1))['status'])
                gateway.heartbeat_master_session(
                    'master-1', master_epoch=started['master_epoch'], now=start + dt.timedelta(seconds=2)
                )
                expired = gateway.watchdog_once(now=start + dt.timedelta(seconds=40))
                self.assertEqual('RESUME_REQUIRED', expired['status'])
                replacement = gateway.start_master_session('master-2', now=start + dt.timedelta(seconds=41))
                self.assertEqual(started['master_epoch'] + 1, replacement['master_epoch'])
            finally:
                gateway.close()

    def test_two_claims_prepare_distinct_worker_browser_intents(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            engine = FakeEngine()
            gateway = V4BridgeGateway(root / 'state.sqlite3', 'project-workers', [worktree], engine)
            try:
                gateway.ensure_contract({'objective': 'two workers'}, {'required': ['AC-WORKERS']})
                gateway.enqueue_graph([
                    {'task_id': 'T1', 'objective_sha256': '1' * 64, 'resource_scope': [worktree / 'a.txt'], 'dependencies': []},
                    {'task_id': 'T2', 'objective_sha256': '2' * 64, 'resource_scope': [worktree / 'b.txt'], 'dependencies': []},
                ])
                claims = gateway.claim_workers(master_epoch=0, limit=2)
                self.assertEqual(2, len(claims))
                recovered = gateway.load_worker_claims(master_epoch=0)
                self.assertEqual(
                    [claim.assignment_id for claim in claims],
                    [claim.assignment_id for claim in recovered],
                )
                first = gateway.submit_worker_intent(claims[0], 'Complete T1 and return WORK_RESULT/1')
                second = gateway.submit_worker_intent(claims[1], 'Complete T2 and return WORK_RESULT/1')
                self.assertEqual('COMPLETED', gateway.store.get_outbox_for_intent(first['intent_id'])['state'])
                self.assertEqual('COMPLETED', gateway.store.get_outbox_for_intent(second['intent_id'])['state'])
                self.assertEqual(2, engine.submits)
                self.assertEqual({'worker/worker-slot-1', 'worker/worker-slot-2'}, {item['channel'] for item in engine.worker_intents})
                self.assertEqual(2, len({item['actor_id'] for item in engine.worker_intents}))
            finally:
                gateway.close()

    def test_completion_check_is_read_only_and_blocks_missing_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            gateway = V4BridgeGateway(
                root / 'state.sqlite3', 'project-completion', [worktree], FakeEngine()
            )
            try:
                gateway.ensure_contract(
                    {'objective': 'completion gate'}, {'required': ['AC-GATE']}
                )
                gateway.store.record_release_candidate(
                    'project-completion',
                    candidate_commit='a' * 40,
                    manifest={'files': {'artifact': 'b' * 64}},
                )
                before = gateway.describe()
                decision = gateway.evaluate_completion(
                    candidate_commit='a' * 40,
                    artifact_hashes={'artifact': 'b' * 64},
                )
                self.assertEqual('BLOCKED', decision.status.value)
                self.assertTrue(any('MISSING_EVIDENCE:AC-GATE' == item for item in decision.blockers))
                after = gateway.describe()
                self.assertEqual(before['state_version'], after['state_version'])
                self.assertEqual('ACTIVE', after['status'])
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
