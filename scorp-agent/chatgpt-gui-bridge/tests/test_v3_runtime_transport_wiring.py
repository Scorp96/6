import tempfile
import types
import unittest
from unittest import mock

import bridge_worker
from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3


class V3RuntimeTransportWiringTests(unittest.TestCase):
    def _args(self, root):
        return types.SimpleNamespace(
            control_repo='Scorp96/scorp-control-plane',
            orchestrator_root=root,
            state_root=root,
            bridge_root=root,
            trusted_actor='Scorp96',
            v3_project_root=root,
            v3_max_inflight=3,
            v3_transport='chrome-use',
            chrome_use_executable=r'C:\\fake\\chrome-use.exe',
            v3_transport_timeout_seconds=30,
            chrome_use_interactive=False,
        )

    def test_build_runtime_injects_explicit_chrome_use_driver(self):
        with tempfile.TemporaryDirectory() as td:
            args=self._args(td)
            with mock.patch('production_v3_runtime.ProductionV3Runtime') as runtime_cls:
                bridge_worker.build_runtime_from_args(args)
            self.assertEqual(1, runtime_cls.call_count)
            kwargs=runtime_cls.call_args.kwargs
            self.assertIn('driver', kwargs)
            self.assertIsInstance(kwargs['driver'], ChromeUseActorDriverV3)

    def test_parser_requires_explicit_transport_choice(self):
        source=__import__('inspect').getsource(bridge_worker.main)
        self.assertIn('--v3-transport', source)
        self.assertIn('--chrome-use-executable', source)
        self.assertIn('--chrome-use-interactive', source)


if __name__=='__main__':
    unittest.main()
