import pathlib,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from chat_resource_manager_v3 import ChatResourceManagerV3
from project_bootstrap_v3 import ProjectBootstrapV3
from production_v3_runtime import ProductionV3Runtime
class ProductionChatResourceWiringTests(unittest.TestCase):
 def test_runtime_injects_durable_chat_resource_manager_into_parallel_relay(self):
  with tempfile.TemporaryDirectory() as td:
   root=pathlib.Path(td)
   ProjectBootstrapV3(root).create(project_id='prod-chat-resource',root_objective='objective',acceptance_criteria='acceptance')
   runtime=ProductionV3Runtime(root,driver=object())
   coordinator=runtime._build()
   manager=coordinator.relay.chat_resources
   self.assertIsInstance(manager,ChatResourceManagerV3)
   self.assertEqual(root/'chat-resource-v3.json',manager.path)
 def test_installer_packages_chat_resource_manager(self):
  installer=(pathlib.Path(__file__).resolve().parents[1]/'install-bridge.ps1').read_text(encoding='utf-8')
  self.assertIn('chat_resource_manager_v3.py',installer)
if __name__=='__main__': unittest.main()