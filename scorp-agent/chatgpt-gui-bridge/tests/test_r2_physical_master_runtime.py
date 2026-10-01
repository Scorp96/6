from __future__ import annotations

import pathlib
import unittest

from v4_physical_rebind import ReadOnlyBrowserRebinder


class R2PhysicalMasterRuntimeTests(unittest.TestCase):
    def test_verify_current_persists_daemon_epoch_and_session_without_rotation(self):
        url = "https://chatgpt.com/c/master-r2"
        session = "scorp-p0-conv-r2"
        calls = []

        class Store:
            def get_browser_binding(self, _project_id, _channel):
                return {
                    "project_id": "p",
                    "channel": "master",
                    "actor_id": "A",
                    "conversation_url": url,
                    "generation": 5,
                }

        class Adapter:
            def rebind(self, *args, **kwargs):
                calls.append((args, kwargs))
                return {
                    "project_id": "p",
                    "channel": "master",
                    "actor_id": "A",
                    "conversation_url": url,
                    "generation": 5,
                    "evidence_json": "{}",
                }

        class Driver:
            async def observe_current_binding(self, _channel):
                return {
                    "driver_url": url,
                    "physical_url": url,
                    "snapshot": "Focused Window: Chrome\n" + url + "\nReady",
                    "session": session,
                }

        rebinder = ReadOnlyBrowserRebinder(
            store=Store(),
            browser_adapter=Adapter(),
            driver=Driver(),
            auth_probe=lambda _channel: {"status": "AUTHENTICATED"},
            project_id="p",
        )
        result = rebinder.verify_current(
            daemon_epoch=9,
            master_epoch=4,
        )
        self.assertEqual("VERIFIED", result["status"])
        self.assertEqual(url, result["conversation_url"])
        self.assertEqual(1, len(calls))
        _args, kwargs = calls[0]
        self.assertEqual(url, kwargs["conversation_url"])
        self.assertEqual(url, kwargs["predecessor_url"])
        self.assertEqual("PHYSICAL_SESSION_VERIFIED", kwargs["reason"])
        self.assertEqual(9, kwargs["evidence"]["daemon_epoch"])
        self.assertEqual(4, kwargs["evidence"]["master_epoch"])
        self.assertEqual(session, kwargs["evidence"]["session"])
        self.assertEqual(5, kwargs["evidence"]["binding_generation"])
        self.assertTrue(kwargs["evidence"]["verified_at"])

    def test_daemon_runtime_registers_verify_master_after_daemon_epoch_is_acquired(self):
        runtime = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v4_daemon_runtime.py"
        text = runtime.read_text(encoding="utf-8")
        self.assertIn('"VERIFY_MASTER"', text)
        self.assertIn("verify_current(", text)
        self.assertLess(
            text.index("daemon_epoch = int(lease"),
            text.index('"VERIFY_MASTER"'),
        )


if __name__ == "__main__":
    unittest.main()
