import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest
import datetime as dt

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


class StructuredExecutionWorkerEngine(StructuredWorkerEngine):
    def __init__(self, source_path):
        super().__init__()
        self.source_path = pathlib.Path(source_path)

    def submit(self, intent):
        response = super().submit(intent)
        result = response['response']
        result['execution_request'] = {
            'module': 'master_a_dynamic_v4.csv_workload.cli',
            'args': [str(self.source_path)],
            'working_directory': str(self.source_path.parent),
            'resource_paths': [str(self.source_path)],
            'access_mode': 'read',
            'timeout_seconds': 10,
        }
        # The controller adds the local receipt before SQLite validation.
        from master_a_dynamic_v4.work_result import result_content_sha256
        result['result_sha256'] = result_content_sha256(result)
        response['response'] = result
        return response


class StructuredGitExecutionWorkerEngine(StructuredWorkerEngine):
    def __init__(self, repository, worktree, base_commit):
        super().__init__()
        self.repository = pathlib.Path(repository)
        self.worktree = pathlib.Path(worktree)
        self.base_commit = base_commit

    def submit(self, intent):
        response = super().submit(intent)
        result = response['response']
        source = self.worktree / 'orders.csv'
        result['execution_request'] = {
            'module': 'master_a_dynamic_v4.csv_workload.cli',
            'args': [str(source)],
            'working_directory': str(self.worktree),
            'resource_paths': [str(source)],
            'access_mode': 'write',
            'timeout_seconds': 30,
            'repository': str(self.repository),
            'worktree': str(self.worktree),
            'base_commit': self.base_commit,
        }
        from master_a_dynamic_v4.work_result import result_content_sha256
        result['result_sha256'] = result_content_sha256(result)
        response['response'] = result
        return response


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
                    None,
                    max_cycles=4,
                )
                self.assertEqual(['DISPATCHED', 'DISPATCHED', 'IDLE'], [item.status for item in history])
                self.assertEqual(2, len(history[0].outcomes))
                self.assertEqual({'T1', 'T2'}, {item['task_id'] for item in history[0].outcomes})
                self.assertEqual(['T3'], [item['task_id'] for item in history[1].outcomes])
                self.assertEqual({'ACCEPTED'}, {gateway.scheduler.get_task(task)['state'] for task in ('T1', 'T2', 'T3')})
                with gateway.store._connection() as conn:
                    verified = conn.execute(
                        "SELECT COUNT(*) FROM events WHERE project_id=? AND kind='WORK_RESULT_INDEPENDENTLY_VERIFIED'",
                        ('project-controller',),
                    ).fetchone()[0]
                self.assertEqual(3, verified)
            finally:
                gateway.close()

    def test_master_controller_executes_allowlisted_local_worker_request(self):
        from master_a_dynamic_v4.execution_adapter import LocalExecutionAdapter
        from master_a_dynamic_v4.master_controller import MasterAController

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            source = worktree / 'orders.csv'
            source.write_text('order_id,category,amount\nO-1,alpha,1.20\n', encoding='utf-8', newline='')
            engine = StructuredExecutionWorkerEngine(source)
            gateway = V4BridgeGateway(root / 'state.sqlite3', 'project-exec-controller', [worktree], engine)
            adapter = LocalExecutionAdapter([worktree], python_executable=sys.executable, pythonpath=sys_path)
            try:
                controller = MasterAController(gateway, 'master-exec-controller', execution_adapter=adapter)
                controller.start({'objective': 'run allowlisted CSV worker'}, {'required': ['AC-EXEC']})
                controller.apply_plan({
                    'project_id': 'project-exec-controller',
                    'master_identity': 'A',
                    'tasks': [{
                        'task_id': 'T1',
                        'objective_sha256': '1' * 64,
                        'resource_scope': [source],
                        'access_mode': 'read',
                        'task_context': {
                            'execution_request_template': {
                                'module': 'master_a_dynamic_v4.csv_workload.cli',
                                'args': [str(source)],
                                'working_directory': str(source.parent),
                                'resource_paths': [str(source)],
                                'access_mode': 'read',
                                'timeout_seconds': 10,
                            },
                        },
                        'dependencies': [],
                        'acceptance_criteria_ids': ['AC-EXEC'],
                    }],
                })
                history = controller.run_cycles(lambda claim: 'return WORK_RESULT/1', max_cycles=2)
                self.assertEqual(['DISPATCHED', 'IDLE'], [item.status for item in history])
                self.assertEqual('ACCEPTED', gateway.scheduler.get_task('T1')['state'])
                with gateway.store._connection() as conn:
                    result_row = conn.execute(
                        "SELECT payload_json FROM candidate_results WHERE project_id=?",
                        ('project-exec-controller',),
                    ).fetchone()
                self.assertIsNotNone(result_row)
                self.assertIn('execution_receipt', json.loads(result_row['payload_json']))
                with gateway.store._connection() as conn:
                    execution_intent = conn.execute(
                        """
                        SELECT i.state,o.state AS outbox_state
                        FROM action_intents i JOIN outbox o ON o.intent_id=i.intent_id
                        WHERE i.project_id=? AND i.action_kind='LOCAL_EXECUTION'
                        """,
                        ('project-exec-controller',),
                    ).fetchone()
                self.assertEqual(('RESPONSE_CAPTURED', 'COMPLETED'), tuple(execution_intent))
                self.assertEqual(1, engine.submits)
            finally:
                gateway.close()

    def test_write_worker_uses_verified_detached_git_worktree_before_execution(self):
        from master_a_dynamic_v4.execution_adapter import LocalExecutionAdapter
        from master_a_dynamic_v4.git_worktree import GitWorktreeManager
        from master_a_dynamic_v4.master_controller import MasterAController

        def git(args, cwd):
            return subprocess.run(
                ['git', *args], cwd=str(cwd), check=True, capture_output=True,
                text=True, encoding='utf-8', errors='replace',
            )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            repository = root / 'repository'; repository.mkdir()
            git(['init', '--initial-branch=main', str(repository)], root)
            git(['-C', str(repository), 'config', 'user.email', 'test@example.invalid'], root)
            git(['-C', str(repository), 'config', 'user.name', 'SCORP Test'], root)
            (repository / 'orders.csv').write_text(
                'order_id,category,amount\nO-1,alpha,1.20\n', encoding='utf-8', newline=''
            )
            git(['-C', str(repository), 'add', 'orders.csv'], root)
            git(['-C', str(repository), 'commit', '-m', 'baseline'], root)
            base_commit = git(['-C', str(repository), 'rev-parse', 'HEAD'], root).stdout.strip()
            worktree = root / 'worker-worktree'
            engine = StructuredGitExecutionWorkerEngine(repository, worktree, base_commit)
            gateway = V4BridgeGateway(
                root / 'state.sqlite3', 'project-git-execution', [root], engine
            )
            controller = MasterAController(
                gateway,
                'master-git-execution',
                execution_adapter=LocalExecutionAdapter([root], python_executable=sys.executable, pythonpath=sys_path),
                git_worktree_manager=GitWorktreeManager([root]),
            )
            try:
                controller.start({'objective': 'run a write-scoped worker'}, {'required': ['AC-GIT']})
                controller.apply_plan({
                    'project_id': 'project-git-execution',
                    'master_identity': 'A',
                    'tasks': [{
                        'task_id': 'T1',
                        'objective_sha256': '1' * 64,
                        'resource_scope': [worktree],
                        'access_mode': 'write',
                        'task_context': {
                            'repository_root': str(repository),
                            'execution_request_template': {
                                'module': 'master_a_dynamic_v4.csv_workload.cli',
                                'args': [str(worktree / 'orders.csv')],
                                'working_directory': str(worktree),
                                'resource_paths': [str(worktree / 'orders.csv')],
                                'access_mode': 'write',
                                'timeout_seconds': 30,
                                'repository': str(repository),
                                'worktree': str(worktree),
                                'base_commit': base_commit,
                            },
                        },
                        'dependencies': [],
                        'acceptance_criteria_ids': ['AC-GIT'],
                    }],
                })
                history = controller.run_cycles(lambda claim: 'return WORK_RESULT/1', max_cycles=2)
                self.assertEqual(['DISPATCHED', 'IDLE'], [item.status for item in history])
                self.assertEqual('ACCEPTED', gateway.scheduler.get_task('T1')['state'])
                self.assertEqual(base_commit, git(['-C', str(worktree), 'rev-parse', 'HEAD'], root).stdout.strip())
                self.assertTrue((worktree / 'orders.csv').is_file())
                with gateway.store._connection() as conn:
                    row = conn.execute(
                        "SELECT payload_json FROM candidate_results WHERE project_id=?",
                        ('project-git-execution',),
                    ).fetchone()
                payload = json.loads(row['payload_json'])
                self.assertEqual(base_commit, payload['execution_receipt']['git_worktree_receipt']['head_commit'])
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

    def test_master_graph_admission_rolls_back_state_when_graph_materialization_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            gateway = V4BridgeGateway(root / 'state.sqlite3', 'project-atomic-graph', [worktree], FakeEngine())
            try:
                gateway.ensure_contract({'objective': 'atomic graph'}, {'required': ['AC-GRAPH']})
                # Seed a task identity that will deliberately conflict during
                # the same transaction as the proposal admission.
                gateway.enqueue_graph([{
                    'task_id': 'T1', 'objective_sha256': '1' * 64,
                    'resource_scope': [worktree / 'a.txt'], 'dependencies': [],
                }])
                before = gateway.store.get_project_state('project-atomic-graph')
                proposal = {
                    'project_id': 'project-atomic-graph', 'master_identity': 'A',
                    'kind': 'TASK_GRAPH', 'task_ids': ['T1'], 'plan_sha256': '2' * 64,
                }
                with self.assertRaisesRegex(Exception, 'TASK_IDENTITY_CONFLICT'):
                    gateway.commit_master_proposal_and_enqueue(
                        'transition-atomic-failure', proposal, [{
                            'task_id': 'T1', 'objective_sha256': '2' * 64,
                            'resource_scope': [worktree / 'a.txt'], 'dependencies': [],
                        }], expected_version=before['state_version'], master_epoch=before['master_epoch']
                    )
                after = gateway.store.get_project_state('project-atomic-graph')
                self.assertEqual(before['state_version'], after['state_version'])
                self.assertEqual(0, gateway.store.count_committed_transitions('project-atomic-graph'))
            finally:
                gateway.close()

    def test_master_graph_admission_rolls_back_after_proposal_commit_failpoint(self):
        """A crash immediately after the state update cannot leave a graph half-state."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / 'worktree'; worktree.mkdir()
            gateway = V4BridgeGateway(root / 'state.sqlite3', 'project-atomic-crash', [worktree], FakeEngine())
            try:
                gateway.ensure_contract({'objective': 'atomic crash'}, {'required': ['AC-GRAPH-CRASH']})
                before = gateway.store.get_project_state('project-atomic-crash')
                gateway.store.failpoint = 'after_plan_state_update'
                proposal = {
                    'project_id': 'project-atomic-crash', 'master_identity': 'A',
                    'kind': 'TASK_GRAPH', 'task_ids': ['T1'], 'plan_sha256': '3' * 64,
                }
                with self.assertRaisesRegex(Exception, 'INJECTED_PLAN_GRAPH_CRASH'):
                    gateway.commit_master_proposal_and_enqueue(
                        'transition-atomic-crash', proposal, [{
                            'task_id': 'T1', 'objective_sha256': '3' * 64,
                            'resource_scope': [worktree / 'a.txt'], 'dependencies': [],
                        }], expected_version=before['state_version'], master_epoch=before['master_epoch']
                    )
                after = gateway.store.get_project_state('project-atomic-crash')
                snapshot = gateway.store.runtime_snapshot('project-atomic-crash', daemon_epoch=0)
                self.assertEqual(before['state_version'], after['state_version'])
                self.assertEqual(0, gateway.store.count_committed_transitions('project-atomic-crash'))
                self.assertEqual({'queued': 0, 'running': 0}, snapshot['tasks'])
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
            engine = StructuredWorkerEngine()
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
                renewed = gateway.renew_worker_lease(
                    claims[0],
                    lease_seconds=60,
                    physical_health={
                        'assignment_id': claims[0].assignment_id,
                        'worker_id': claims[0].worker_id,
                        'session_id': 'worker/worker-slot-1',
                        'status': 'READY',
                        'observed_at': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
                    },
                )
                self.assertEqual("ACTIVE", renewed["state"])
                self.assertTrue(renewed["heartbeat_at"])
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
