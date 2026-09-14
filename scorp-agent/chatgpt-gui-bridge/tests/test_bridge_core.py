import json, unittest, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bridge_core import find_chat_editor, response_is_terminal, extract_mutation, build_bound_decision

REQ={
 'protocol_version':'scorp.orchestrator/continuation-request-v1',
 'request_id':'cont-test-001','task_id':'task-1','project_id':'proj-1',
 'registry_sequence':12,'continuation_generation':3,'question':'choose next',
 'safety_class':'standard','previous_action_id':'action-1',
 'previous_result_sha256':'a'*64,'created_at':'2026-09-10T10:00:00Z','expires_at':'2026-09-11T10:00:00Z'
}

class CoreTests(unittest.TestCase):
 def test_find_chat_editor_cn(self):
  s='(1227,981) 编辑 "与 ChatGPT 聊天"  [action: click]  [value:"问问 ChatGPT"]'
  self.assertEqual(find_chat_editor(s),(1227,981))
 def test_find_chat_editor_en(self):
  s='(900,700) edit "Message ChatGPT" [action: click]'
  self.assertEqual(find_chat_editor(s),(900,700))
 def test_streaming_is_not_terminal(self):
  self.assertFalse(response_is_terminal('按钮 "停止回答" text SCORP_GUI_MUTATION_V1::cont-test-001::{}'))
 def test_final_is_terminal(self):
  self.assertTrue(response_is_terminal('按钮 "启动语音功能" text reply'))
 def test_extract_exact_marker(self):
  s='text "SCORP_GUI_MUTATION_V1::cont-test-001::{\\"kind\\":\\"SET_TERMINAL\\",\\"state\\":\\"DONE\\"}"'
  self.assertEqual(extract_mutation(s,'cont-test-001')['kind'],'SET_TERMINAL')
 def test_duplicate_identical_ui_copies_are_accepted(self):
  marker='SCORP_GUI_MUTATION_V1::cont-test-001::{"kind":"SET_TERMINAL","state":"DONE"}'
  s='group "'+marker+'"\ntext "'+marker+'"'
  self.assertEqual(extract_mutation(s,'cont-test-001')['kind'],'SET_TERMINAL')
 def test_conflicting_ui_copies_rejected(self):
  a='SCORP_GUI_MUTATION_V1::cont-test-001::{"kind":"SET_TERMINAL","state":"DONE"}'
  b='SCORP_GUI_MUTATION_V1::cont-test-001::{"kind":"SET_WAITING","reason":"x"}'
  with self.assertRaises(ValueError): extract_mutation(a+'\n'+b,'cont-test-001')
 def test_no_marker_rejected(self):
  with self.assertRaises(ValueError): extract_mutation('no response marker','cont-test-001')
 def test_nonstandard_rejected(self):
  r=dict(REQ); r['safety_class']='approved_admin'
  with self.assertRaises(ValueError): build_bound_decision(r,{'kind':'SET_TERMINAL','state':'DONE'})
 def test_binding(self):
  d=build_bound_decision(REQ,{'kind':'SET_TERMINAL','state':'DONE'})
  self.assertEqual(d['request_id'],'cont-test-001'); self.assertEqual(d['task_id'],'task-1')
  self.assertEqual(d['expected_registry_sequence'],12); self.assertEqual(d['previous_continuation_generation'],3)
  self.assertEqual(d['previous_action_id'],'action-1'); self.assertEqual(d['previous_result_sha256'],'a'*64)
  self.assertEqual(d['mutation']['kind'],'SET_TERMINAL')

if __name__=='__main__': unittest.main()
