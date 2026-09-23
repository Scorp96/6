from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from bridge_worker import build_runtime_from_args, build_v3_driver
from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3


class V3TransportSelectionTests(unittest.TestCase):
    def test_chrome_use_builds_chrome_use_driver(self):
        with tempfile.TemporaryDirectory() as td:
            driver = build_v3_driver(
                transport="chrome-use",
                project_root=td,
                chrome_use_executable=r"C:\fake\chrome-use.exe",
            )
            self.assertIsInstance(driver, ChromeUseActorDriverV3)
            self.assertEqual(driver.state_path, Path(td) / "chrome-use-driver-v3.json")
            self.assertFalse(driver.cli.interactive)

    def test_chrome_use_foregrounding_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            driver = build_v3_driver(
                transport="chrome-use",
                project_root=td,
                chrome_use_executable=r"C:\fake\chrome-use.exe",
                chrome_use_interactive=True,
            )
            self.assertTrue(driver.cli.interactive)

    def test_missing_chrome_use_executable_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "CHROME_USE_EXECUTABLE_REQUIRED"):
                build_v3_driver(
                    transport="chrome-use",
                    project_root=td,
                    chrome_use_executable=None,
                )

    def test_windows_mcp_requires_explicit_legacy_selection(self):
        with tempfile.TemporaryDirectory() as td:
            driver = build_v3_driver(
                transport="windows-mcp",
                project_root=td,
                chrome_use_executable=None,
            )
            self.assertIsInstance(driver, WindowsMcpActorDriverV3)

    def test_unknown_transport_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "V3_TRANSPORT_INVALID"):
                build_v3_driver(
                    transport="auto",
                    project_root=td,
                    chrome_use_executable=None,
                )

    def test_runtime_injects_selected_driver_into_production_v3(self):
        sentinel_driver = object()
        sentinel_v3 = object()
        args = SimpleNamespace(
            control_repo="Scorp96/scorp-control-plane",
            orchestrator_root=r"C:\orchestrator",
            state_root=r"C:\state-v4",
            bridge_root=r"C:\bridge-state",
            trusted_actor="Scorp96",
            v3_project_root=r"C:\isolated-v3",
            v3_max_inflight=3,
            v3_transport="chrome-use",
            chrome_use_executable=r"C:\chrome-use\chrome-use.exe",
            v3_transport_timeout_seconds=17,
        )
        with patch("bridge_worker.build_v3_driver", return_value=sentinel_driver) as build_driver:
            with patch("production_v3_runtime.ProductionV3Runtime", return_value=sentinel_v3) as runtime_cls:
                runtime = build_runtime_from_args(args)
        build_driver.assert_called_once_with(
            "chrome-use",
            r"C:\isolated-v3",
            r"C:\chrome-use\chrome-use.exe",
            timeout_seconds=17,
            chrome_use_interactive=False,
        )
        runtime_cls.assert_called_once_with(
            r"C:\isolated-v3",
            max_inflight=3,
            max_workers=4,
            driver=sentinel_driver,
            adaptive_browser_poll=True,
        )
        self.assertIs(runtime.v3_runtime, sentinel_v3)


if __name__ == "__main__":
    unittest.main()
