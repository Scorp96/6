from __future__ import annotations

import pathlib
import tempfile
import unittest


class RuntimeFilesetTests(unittest.TestCase):
    def _seed(self, root: pathlib.Path) -> None:
        required = [
            "scorp-agent/master_a_dynamic_v4/__init__.py",
            "scorp-agent/master_a_dynamic_v4/runtime_protocol.py",
            "scorp-agent/master_a_dynamic_v4/runtime_commands.py",
            "scorp-agent/master_a_dynamic_v4/operator_control.py",
            "scorp-agent/master_a_dynamic_v4/runtime_pipe.py",
            "scorp-agent/master_a_dynamic_v4/production_bootstrap.py",
            "scorp-agent/master_a_dynamic_v4/schema.sql",
            "scorp-agent/chatgpt-gui-bridge/tools/v4_daemon_runtime.py",
            "scorp-agent/chatgpt-gui-bridge/tools/v4_release_runtime.py",
            "scorp-agent/chatgpt-gui-bridge/tools/v4_master_controller_runtime.py",
            "scorp-agent/chatgpt-gui-bridge/v4_browser_engine.py",
            "scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py",
            "scorp-agent/chatgpt-gui-bridge/chrome_use_actor_driver_v3.py",
            "scorp-agent/chatgpt-gui-bridge/install-v4-daemon.ps1",
            "scorp-agent/chatgpt-gui-bridge/watch-v4-daemon.ps1",
        ]
        for rel in required:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rel, encoding="utf-8")

    def test_fileset_includes_complete_runtime_package_and_excludes_tests_and_caches(self):
        from master_a_dynamic_v4.install_manifest import v4_runtime_release_paths
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._seed(root)
            extra = root / "scorp-agent/master_a_dynamic_v4/runtime_connector_cli.py"
            extra.write_text("runtime connector", encoding="utf-8")
            ignored = root / "scorp-agent/master_a_dynamic_v4/tests/test_fake.py"
            ignored.parent.mkdir(parents=True, exist_ok=True)
            ignored.write_text("ignored", encoding="utf-8")
            cache = root / "scorp-agent/chatgpt-gui-bridge/__pycache__/fake.pyc"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(b"x")
            files = set(v4_runtime_release_paths(root))
            self.assertIn("scorp-agent/master_a_dynamic_v4/runtime_protocol.py", files)
            self.assertIn("scorp-agent/master_a_dynamic_v4/runtime_commands.py", files)
            self.assertIn("scorp-agent/master_a_dynamic_v4/operator_control.py", files)
            self.assertIn("scorp-agent/master_a_dynamic_v4/runtime_connector_cli.py", files)
            self.assertNotIn("scorp-agent/master_a_dynamic_v4/tests/test_fake.py", files)
            self.assertNotIn("scorp-agent/chatgpt-gui-bridge/__pycache__/fake.pyc", files)

    def test_missing_required_runtime_entrypoint_fails_closed(self):
        from master_a_dynamic_v4.install_manifest import InstallIdentityError, v4_runtime_release_paths
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._seed(root)
            (root / "scorp-agent/master_a_dynamic_v4/runtime_protocol.py").unlink()
            with self.assertRaisesRegex(InstallIdentityError, "V4_RUNTIME_REQUIRED_FILE_MISSING"):
                v4_runtime_release_paths(root)


if __name__ == "__main__":
    unittest.main()
