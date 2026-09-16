from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from master_a_dynamic_v4.state_store import StateStore
from tools import v4_daemon_runtime as runtime


class V4DaemonActiveControllerRuntimeIntegrationTests(unittest.TestCase):
    def test_active_controller_installs_persistent_controller_actions_into_daemon(self):
        captured = {}
        real_daemon = runtime.LocalDaemon

        class CapturingDaemon(real_daemon):
            def __init__(self, *args, action_handlers=None, **kwargs):
                captured.update(dict(action_handlers or {}))
                super().__init__(*args, action_handlers=action_handlers, **kwargs)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / 'state.sqlite3'
            driver = root / 'driver.json'
            driver.write_text('{}\n', encoding='utf-8')
            with StateStore(db, [root]) as store:
                store.create_contract('p', root_contract={'objective':'x'}, acceptance_contract={'ids':[]})
                store.start_master_session('p', 'master-a', ttl_seconds=60)
            args = runtime.build_parser().parse_args([
                '--database-path', str(db),
                '--allowed-root', str(root),
                '--project-id', 'p',
                '--health-path', str(root / 'health.json'),
                '--max-iterations', '1',
                '--supervise-master',
                '--master-session-id', 'master-a',
                '--active-controller',
                '--driver-state-path', str(driver),
            ])
            with mock.patch.object(runtime, 'LocalDaemon', CapturingDaemon):
                with contextlib.redirect_stdout(io.StringIO()):
                    runtime.run_runtime(args)
        required = {
            'RECONCILE_AMBIGUOUS',
            'ASSIGN_WORKER',
            'WAKE_MASTER',
            'RESUME_WORKER',
            'RECOVER_STALLED',
        }
        self.assertTrue(required.issubset(set(captured)), sorted(captured))


if __name__ == '__main__':
    unittest.main()