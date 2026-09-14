import tempfile
import unittest
from unittest.mock import AsyncMock
from pathlib import Path

from bridge_worker import ConversationRegistry, BridgeRuntime


class ConversationRegistryTests(unittest.TestCase):
    def test_persists_and_reloads_task_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'conversation-registry.json'
            reg = ConversationRegistry(path)
            self.assertIsNone(reg.get_url('task-a'))
            reg.record('task-a', 'req-1', 'dec-1', 'https://chatgpt.com/c/abc-123')
            reloaded = ConversationRegistry(path)
            self.assertEqual(reloaded.get_url('task-a'), 'https://chatgpt.com/c/abc-123')

    def test_task_ownership_is_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            reg = ConversationRegistry(Path(td) / 'conversation-registry.json')
            reg.record('task-a', 'req-a', 'dec-a', 'https://chatgpt.com/c/a-1')
            reg.record('task-b', 'req-b', 'dec-b', 'https://chatgpt.com/c/b-1')
            self.assertEqual(reg.get_url('task-a'), 'https://chatgpt.com/c/a-1')
            self.assertEqual(reg.get_url('task-b'), 'https://chatgpt.com/c/b-1')

class RuntimeConversationReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_passes_saved_task_conversation_to_gui_turn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orch = root / 'orch'; state = root / 'state'; bridge = root / 'bridge'
            orch.mkdir(); state.mkdir(); bridge.mkdir()
            request = {'request_id':'req-2','protocol_version':'scorp.orchestrator/continuation-request-v1','task_id':'task-a','project_id':'proj','continuation_generation':2,'registry_sequence':7,'previous_action_id':None,'previous_result_sha256':None,'issue_number':2,'publication_state':'PUBLISHED','safety_class':'standard','expires_at':'2099-01-01T00:00:00Z','created_at':'2026-01-01T00:00:00Z'}
            task = {'task_id':'task-a','project_id':'proj','state':'WAITING','waiting_reason':'GPT_CONTINUATION_REQUIRED','continuation_request_id':'req-2','generation':2,'safety_class':'standard','authorization_requirement':None,'last_result':{}}
            (orch/'registry.json').write_text(__import__('json').dumps({'tasks':[task]}),encoding='utf-8')
            (orch/'continuation-outbox').mkdir(); (orch/'continuation-outbox'/'req-2.json').write_text(__import__('json').dumps(request),encoding='utf-8')
            ConversationRegistry(bridge/'conversation-registry.json').record('task-a','req-1','dec-1','https://chatgpt.com/c/abc-123')
            class KeywordOnlyGui:
                def __init__(self): self.seen = None
                async def __call__(self, prompt, request_id, *, conversation_url=None):
                    self.seen = conversation_url
                    raise RuntimeError('STOP_AFTER_ARGUMENT_CAPTURE')
            gui = KeywordOnlyGui()
            gh = type('GH',(),{'list_comments':lambda self,n: []})()
            runtime = BridgeRuntime(orch,state,bridge,gh,gui,'Scorp96')
            with self.assertRaisesRegex(RuntimeError,'STOP_AFTER_ARGUMENT_CAPTURE'):
                await runtime.run_once()
            self.assertEqual(gui.seen, 'https://chatgpt.com/c/abc-123')
