from __future__ import annotations

import pathlib
import unittest


class V4DaemonMasterRebindRuntimeIntegrationTests(unittest.TestCase):
    def test_active_daemon_injects_read_only_rebinder_into_master_supervisor(self):
        runtime = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v4_daemon_runtime.py"
        text = runtime.read_text(encoding="utf-8")
        for token in (
            "from v4_physical_rebind import ReadOnlyBrowserRebinder",
            "ReadOnlyBrowserRebinder(",
            "rebind_callback=rebind_callback",
            "physical_health_probe=physical_health_probe",
        ):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()