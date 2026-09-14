import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bridge_worker import select_pending_request, load_previous_result, validate_live_binding, build_decision_comment, find_existing_decision, obtain_and_publish_decision
from bridge_core import build_bound_decision

NOW=dt.datetime(2026,9,10,10,0,0,tzinfo=dt.timezone.utc)
REQ={"protocol_version":"scorp.orchestrator/continuation-request-v1","request_id":"cont-w1","task_id":"task-w1","project_id":"p","registry_sequence":5,"continuation_generation":2,"question":"next","safety_class":"standard","previous_action_id":"a1","previous_result_sha256":"a"*64,"created_at":"2026-09-10T09:00:00Z","expires_at":"2026-09-11T09:00:00Z","issue_number":12,"publication_state":"PUBLISHED"}
TASK={"task_id":"task-w1","project_id":"p","priority":10,"state":"WAITING","waiting_reason":"GPT_CONTINUATION_REQUIRED","continuation_request_id":"cont-w1","generation":2,"safety_class":"standard","authorization_requirement":None,"last_result":{"action_id":"a1","issue_number":44,"result_sha256":"a"*64}}
REG={"sequence":5,"tasks":[TASK]}

class WorkerPureTests(unittest.TestCase):
 def test_select_pending(self):
  req,task=select_pending_request(REG,[REQ],{},NOW)
  self.assertEqual(req['request_id'],'cont-w1'); self.assertEqual(task['task_id'],'task-w1')
 def test_nonstandard_not_selected(self):
  q=dict(REQ); q['safety_class']='admin'
  self.assertIsNone(select_pending_request(REG,[q],{},NOW))
 def test_authorization_requirement_not_selected(self):
  r=json.loads(json.dumps(REG)); r['tasks'][0]['authorization_requirement']='EXPLICIT'
  self.assertIsNone(select_pending_request(r,[REQ],{},NOW))
 def test_expired_not_selected(self):
  q=dict(REQ); q['expires_at']='2026-09-10T09:59:59Z'
  self.assertIsNone(select_pending_request(REG,[q],{},NOW))
 def test_live_binding_requires_exact_waiting_request(self):
  bad=json.loads(json.dumps(TASK)); bad['continuation_request_id']='other'
  with self.assertRaises(ValueError): validate_live_binding(bad,REQ)
 def test_previous_result_hash_verified(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'issue-44-action-a1-result.json'; raw=b'{"status":"SUCCEEDED","stdout_tail":"A_OK"}'
   p.write_bytes(raw); req=dict(REQ); req['previous_result_sha256']=hashlib.sha256(raw).hexdigest(); task=json.loads(json.dumps(TASK)); task['last_result']['result_sha256']=req['previous_result_sha256']
   self.assertEqual(load_previous_result(task,req,d)['stdout_tail'],'A_OK')
 def test_previous_result_hash_mismatch_fails(self):
  with tempfile.TemporaryDirectory() as d:
   pathlib.Path(d,'issue-44-action-a1-result.json').write_text('{}',encoding='utf-8')
   with self.assertRaises(ValueError): load_previous_result(TASK,REQ,d)
 def test_decision_comment_prefix(self):
  body=build_decision_comment({'protocol_version':'scorp.orchestrator/continuation-decision-v1','decision_id':'d','request_id':'cont-w1','task_id':'task-w1','expected_registry_sequence':5,'previous_continuation_generation':2,'mutation':{'kind':'SET_TERMINAL','state':'DONE'}})
  self.assertTrue(body.startswith('SCORP_CONT_DECISION\n')); json.loads(body.split('\n',1)[1])


class FakeGitHub:
 def __init__(self, comments=None): self.comments=list(comments or []); self.posts=0
 def list_comments(self, issue_number): return list(self.comments)
 def post_comment(self, issue_number, body):
  self.posts+=1; self.comments.append({'user':{'login':'Scorp96'},'body':body}); return {'id':100+self.posts}

class FakeGui:
 def __init__(self, mutation=None, error=None): self.mutation=mutation or {'kind':'SET_TERMINAL','state':'DONE'}; self.error=error; self.calls=0
 async def __call__(self, prompt, request_id, conversation_url=None):
  self.calls+=1
  if self.error: raise self.error
  return self.mutation, 'snapshot', 'https://chatgpt.com/c/test'

class PublishTests(unittest.TestCase):
 def run_publish(self, gh, gui, ledger=None):
  ledger = ledger if ledger is not None else {}
  persisted=[]
  result=__import__('asyncio').run(obtain_and_publish_decision(REQ,TASK,{'status':'SUCCEEDED'},gh,gui,ledger,lambda x: persisted.append(json.loads(json.dumps(x))),lambda: TASK,'Scorp96'))
  return result,ledger,persisted
 def test_existing_decision_adopted_without_gui(self):
  d=build_bound_decision(REQ,{'kind':'SET_TERMINAL','state':'DONE'})
  gh=FakeGitHub([{'user':{'login':'Scorp96'},'body':build_decision_comment(d)}]); gui=FakeGui()
  result,ledger,_=self.run_publish(gh,gui)
  self.assertEqual(result['status'],'ADOPTED'); self.assertEqual(gui.calls,0); self.assertEqual(gh.posts,0); self.assertEqual(ledger['cont-w1']['state'],'ADOPTED')
 def test_first_publish_then_restart_does_not_duplicate(self):
  gh=FakeGitHub(); gui=FakeGui(); ledger={}
  r1,ledger,_=self.run_publish(gh,gui,ledger)
  self.assertEqual(r1['status'],'POSTED'); self.assertEqual(gh.posts,1); self.assertEqual(gui.calls,1)
  gui2=FakeGui(); r2,ledger,_=self.run_publish(gh,gui2,ledger)
  self.assertEqual(r2['status'],'ADOPTED'); self.assertEqual(gh.posts,1); self.assertEqual(gui2.calls,0)
 def test_gui_error_never_posts(self):
  gh=FakeGitHub(); gui=FakeGui(error=RuntimeError('GUI_DOWN'))
  with self.assertRaises(RuntimeError): self.run_publish(gh,gui)
  self.assertEqual(gh.posts,0)
 def test_conflicting_trusted_decisions_fail_closed(self):
  a=build_bound_decision(REQ,{'kind':'SET_TERMINAL','state':'DONE'})
  b=build_bound_decision(REQ,{'kind':'SET_WAITING','reason':'later'}); b['decision_id']='other'
  gh=FakeGitHub([{'user':{'login':'Scorp96'},'body':build_decision_comment(a)},{'user':{'login':'Scorp96'},'body':build_decision_comment(b)}])
  with self.assertRaises(ValueError): find_existing_decision(gh.list_comments(12),REQ,'Scorp96')
if __name__=='__main__': unittest.main()

class RetryStateTests(unittest.TestCase):
 def test_active_gui_inflight_lease_not_selected(self):
  ledger={'cont-w1':{'state':'GUI_INFLIGHT','lease_until':'2026-09-10T10:05:00Z'}}
  self.assertIsNone(select_pending_request(REG,[REQ],ledger,NOW))
 def test_retry_wait_not_selected_before_deadline(self):
  ledger={'cont-w1':{'state':'RETRY_WAIT','next_retry_at':'2026-09-10T10:05:00Z'}}
  self.assertIsNone(select_pending_request(REG,[REQ],ledger,NOW))
 def test_retry_wait_selected_after_deadline(self):
  ledger={'cont-w1':{'state':'RETRY_WAIT','next_retry_at':'2026-09-10T09:59:00Z'}}
  picked=select_pending_request(REG,[REQ],ledger,NOW)
  self.assertEqual(picked[0]['request_id'],'cont-w1')
 def test_response_captured_republishes_without_gui(self):
  decision=build_bound_decision(REQ,{'kind':'SET_TERMINAL','state':'DONE'})
  ledger={'cont-w1':{'state':'RESPONSE_CAPTURED','decision_id':decision['decision_id'],'decision':decision,'conversation_url':'https://chatgpt.com/c/test'}}
  gh=FakeGitHub(); gui=FakeGui()
  result,ledger,_=PublishTests().run_publish(gh,gui,ledger)
  self.assertEqual(result['status'],'POSTED'); self.assertEqual(gui.calls,0); self.assertEqual(gh.posts,1)
 def test_gui_error_enters_retry_wait(self):
  gh=FakeGitHub(); gui=FakeGui(error=RuntimeError('GUI_DOWN')); ledger={}; persisted=[]
  with self.assertRaises(RuntimeError):
   __import__('asyncio').run(obtain_and_publish_decision(REQ,TASK,{'status':'SUCCEEDED'},gh,gui,ledger,lambda x:persisted.append(json.loads(json.dumps(x))),lambda:TASK,'Scorp96'))
  self.assertEqual(ledger['cont-w1']['state'],'RETRY_WAIT')
  self.assertIn('next_retry_at',ledger['cont-w1']); self.assertEqual(gh.posts,0)
