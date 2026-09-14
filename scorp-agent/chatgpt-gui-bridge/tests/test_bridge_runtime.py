import asyncio
import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bridge_worker import BridgeRuntime, atomic_write_json

NOW = dt.datetime(2026, 9, 10, 11, 0, 0, tzinfo=dt.timezone.utc)

class FakeGitHub:
    def __init__(self):
        self.comments = {}
        self.posts = 0
    def list_comments(self, issue_number):
        return list(self.comments.get(issue_number, []))
    def post_comment(self, issue_number, body):
        self.posts += 1
        self.comments.setdefault(issue_number, []).append({
            'user': {'login': 'Scorp96'}, 'body': body
        })
        return {'id': self.posts}

class FakeGui:
    def __init__(self): self.calls = 0
    async def __call__(self, prompt, request_id, conversation_url=None):
        self.calls += 1
        return ({'kind': 'SET_TERMINAL', 'state': 'DONE'},
                'terminal snapshot', 'https://chatgpt.com/c/runtime-test')

def make_fixture(root: pathlib.Path):
    state = root / 'state-v4'; state.mkdir()
    orch = root / 'orch'; orch.mkdir()
    outbox = orch / 'continuation-outbox'; outbox.mkdir()
    bridge = root / 'bridge'; bridge.mkdir()
    raw = b'{"status":"SUCCEEDED","stdout_tail":"A_OK"}'
    digest = hashlib.sha256(raw).hexdigest()
    (state / 'issue-44-action-a1-result.json').write_bytes(raw)
    req = {
        'protocol_version':'scorp.orchestrator/continuation-request-v1',
        'request_id':'cont-runtime-1','task_id':'task-runtime-1','project_id':'p',
        'registry_sequence':5,'continuation_generation':2,'question':'next',
        'safety_class':'standard','previous_action_id':'a1',
        'previous_result_sha256':digest,'created_at':'2026-09-10T10:00:00Z',
        'expires_at':'2026-09-11T10:00:00Z','issue_number':12,
        'publication_state':'PUBLISHED'}
    task = {
        'task_id':'task-runtime-1','project_id':'p','priority':10,'state':'WAITING',
        'waiting_reason':'GPT_CONTINUATION_REQUIRED','continuation_request_id':'cont-runtime-1',
        'generation':2,'safety_class':'standard','authorization_requirement':None,
        'last_result':{'action_id':'a1','issue_number':44,'result_sha256':digest}}
    atomic_write_json(outbox / 'cont-runtime-1.json', req)
    atomic_write_json(orch / 'registry.json', {'protocol_version':'scorp.orchestrator/v1','sequence':5,'tasks':[task]})
    return orch, state, bridge

class RuntimeTests(unittest.TestCase):
    def test_atomic_write_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'x.json'
            atomic_write_json(p, {'a': 1})
            self.assertEqual(json.loads(p.read_text(encoding='utf-8')), {'a': 1})
            self.assertFalse(p.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_atomic_write_json_is_safe_for_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'concurrent.json'
            errors = []
            barrier = threading.Barrier(2)
            def write(value):
                try:
                    barrier.wait()
                    atomic_write_json(p, {'value': value})
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=write, args=(index,)) for index in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual([], errors)
            self.assertIn(json.loads(p.read_text(encoding='utf-8'))['value'], (0, 1))

    def test_run_once_posts_and_persists_health_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            orch,state,bridge = make_fixture(pathlib.Path(d))
            gh=FakeGitHub(); gui=FakeGui()
            rt=BridgeRuntime(str(orch),str(state),str(bridge),gh,gui,'Scorp96')
            result=asyncio.run(rt.run_once(NOW))
            self.assertEqual(result['status'],'POSTED')
            self.assertEqual(gh.posts,1); self.assertEqual(gui.calls,1)
            ledger=json.loads((bridge/'ledger.json').read_text(encoding='utf-8'))
            health=json.loads((bridge/'health.json').read_text(encoding='utf-8'))
            self.assertEqual(ledger['cont-runtime-1']['state'],'POSTED')
            self.assertEqual(health['status'],'POSTED')
            self.assertEqual(health['last_request_id'],'cont-runtime-1')

    def test_restart_adopts_existing_decision_without_second_gui_turn(self):
        with tempfile.TemporaryDirectory() as d:
            orch,state,bridge = make_fixture(pathlib.Path(d))
            gh=FakeGitHub(); first=FakeGui()
            rt=BridgeRuntime(str(orch),str(state),str(bridge),gh,first,'Scorp96')
            asyncio.run(rt.run_once(NOW))
            (bridge/'ledger.json').unlink()
            second=FakeGui()
            rt2=BridgeRuntime(str(orch),str(state),str(bridge),gh,second,'Scorp96')
            result=asyncio.run(rt2.run_once(NOW))
            self.assertEqual(result['status'],'ADOPTED')
            self.assertEqual(second.calls,0)
            self.assertEqual(gh.posts,1)

if __name__ == '__main__':
    unittest.main()

class FakeCompleted:
    def __init__(self, code=0, stdout='', stderr=''):
        self.returncode=code; self.stdout=stdout; self.stderr=stderr

class GitHubCliTests(unittest.TestCase):
    def test_list_comments_uses_control_repo_and_parses_json(self):
        from bridge_worker import GitHubCli
        calls=[]
        def runner(argv, **kwargs):
            calls.append(argv)
            return FakeCompleted(stdout='[{"body":"x","user":{"login":"Scorp96"}}]')
        gh=GitHubCli('Scorp96/scorp-control-plane', runner=runner)
        comments=gh.list_comments(7)
        self.assertEqual(comments[0]['body'],'x')
        self.assertTrue(calls[0][-1].startswith('/repos/Scorp96/scorp-control-plane/issues/7/comments'))

    def test_list_comments_paginates_until_short_page(self):
        from bridge_worker import GitHubCli
        calls=[]
        pages = [[{'id': index} for index in range(100)], [{'id': 100}]]
        def runner(argv, **kwargs):
            calls.append(argv)
            return FakeCompleted(stdout=json.dumps(pages.pop(0)))
        gh=GitHubCli('Scorp96/666', runner=runner)
        comments=gh.list_comments(7)
        self.assertEqual(101, len(comments))
        self.assertEqual(2, len(calls))
        self.assertIn('page=2', calls[1][-1])

    def test_post_comment_uses_utf8_json_file_and_accepts_stderr_on_success(self):
        from bridge_worker import GitHubCli
        seen={}
        def runner(argv, **kwargs):
            seen.update(argv=list(argv), kwargs=kwargs)
            input_path=pathlib.Path(argv[argv.index('--input')+1])
            seen['input_path']=str(input_path)
            seen['payload']=json.loads(input_path.read_text(encoding='utf-8'))
            return FakeCompleted(stdout='{"id":9}', stderr='warning')
        gh=GitHubCli('Scorp96/scorp-control-plane', runner=runner)
        self.assertEqual(gh.post_comment(7,'hello')['id'],9)
        self.assertIn('--input', seen['argv'])
        self.assertNotEqual(seen['argv'][seen['argv'].index('--input')+1], '-')
        self.assertEqual(seen['payload'], {'body':'hello'})
        self.assertFalse(pathlib.Path(seen['input_path']).exists())


class DaemonTests(unittest.TestCase):
    def test_daemon_survives_one_runtime_error_and_continues(self):
        from bridge_worker import run_daemon
        class Flaky:
            def __init__(self): self.calls=0
            async def run_once(self):
                self.calls += 1
                if self.calls == 1: raise RuntimeError('transient')
                return {'status':'IDLE'}
        sleeps=[]
        async def sleeper(seconds): sleeps.append(seconds)
        runtime=Flaky()
        asyncio.run(run_daemon(runtime, poll_seconds=10, max_cycles=2, sleeper=sleeper))
        self.assertEqual(runtime.calls,2)
        self.assertEqual(sleeps,[10])

    def test_daemon_reports_errors_and_stops_after_bounded_failures(self):
        from bridge_worker import run_daemon
        class Broken:
            async def run_once(self): raise RuntimeError('bridge-down')
        errors=[]; sleeps=[]
        async def sleeper(seconds): sleeps.append(seconds)
        asyncio.run(run_daemon(Broken(), poll_seconds=10, max_cycles=5, max_consecutive_failures=2,
                               on_error=lambda exc, cycle: errors.append((str(exc), cycle)), sleeper=sleeper))
        self.assertEqual([('bridge-down', 1), ('bridge-down', 2)], errors)
        self.assertEqual([10], sleeps)
