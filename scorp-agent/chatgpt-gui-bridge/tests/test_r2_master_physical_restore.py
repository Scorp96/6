from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from v4_physical_rebind import ReadOnlyBrowserRebinder


class _RestoreCli:
    def __init__(self, target: str):
        self.target = target
        self.calls = []
        self.current = "https://chatgpt.com/"

    async def run_json(self, session, *args, timeout_seconds=30):
        self.calls.append((session, list(args)))
        if list(args)[:2] == ["get", "url"]:
            return {"data": {"value": self.current}}
        if list(args)[:1] == ["open"]:
            self.current = str(args[1])
            return {"success": True}
        if list(args)[:1] == ["read"]:
            return {"data": {"snapshot": "Ready"}}
        raise AssertionError(args)


class R2MasterPhysicalRestoreTests(unittest.TestCase):
    def test_restore_known_master_binding_opens_only_durable_url_and_never_sends(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "driver.json"
            url = "https://chatgpt.com/c/master-r2-restore"
            cli = _RestoreCli(url)
            driver = ChromeUseActorDriverV3(
                cli,
                state,
                sleeper=lambda _seconds: asyncio.sleep(0),
            )

            observed = asyncio.run(
                driver.restore_known_binding("master", url)
            )

            self.assertEqual(url, observed["driver_url"])
            self.assertEqual(url, observed["physical_url"])
            self.assertTrue(observed["session"].startswith("scorp-p0-conv-"))
            commands = [args for _session, args in cli.calls]
            self.assertIn(["open", url], commands)
            self.assertIn(["read"], commands)
            self.assertFalse(
                any(
                    args and args[0] in {"fill", "click", "press", "type", "paste"}
                    for args in commands
                )
            )

    def test_restore_refuses_conflicting_existing_master_before_navigation(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "driver.json"
            durable = "https://chatgpt.com/c/master-r2-durable"
            conflicting = "https://chatgpt.com/c/master-r2-conflict"
            cli = _RestoreCli(durable)
            driver = ChromeUseActorDriverV3(
                cli,
                state,
                sleeper=lambda _seconds: asyncio.sleep(0),
            )
            driver.bind_turn("existing-master", conflicting, actor_kind="MASTER")

            with self.assertRaisesRegex(
                ValueError,
                "CHROME_USE_PHYSICAL_BINDING_CONFLICT",
            ):
                asyncio.run(driver.restore_known_binding("master", durable))

            self.assertEqual([], cli.calls)

    def test_verify_current_restores_missing_physical_session_without_send(self):
        url = "https://chatgpt.com/c/master-r2-restore"
        session = "scorp-p0-conv-restore"
        persisted = []

        class Store:
            def get_browser_binding(self, _project_id, _channel):
                return {
                    "project_id": "p",
                    "channel": "master",
                    "actor_id": "A",
                    "conversation_url": url,
                    "generation": 2,
                }

        class Driver:
            def __init__(self):
                self.restore_calls = []

            async def observe_current_binding(self, _channel):
                raise RuntimeError("physical session missing")

            async def restore_known_binding(self, channel, conversation_url):
                self.restore_calls.append((channel, conversation_url))
                return {
                    "driver_url": url,
                    "physical_url": url,
                    "snapshot": "Focused Window: Chrome\n" + url + "\nReady",
                    "session": session,
                }

        class Adapter:
            def rebind(self, *args, **kwargs):
                persisted.append((args, kwargs))
                return {
                    "project_id": "p",
                    "channel": "master",
                    "actor_id": "A",
                    "conversation_url": url,
                    "generation": 2,
                    "evidence_json": "{}",
                }

        driver = Driver()
        rebinder = ReadOnlyBrowserRebinder(
            store=Store(),
            browser_adapter=Adapter(),
            driver=driver,
            auth_probe=lambda _channel: {"status": "AUTHENTICATED"},
            project_id="p",
        )

        result = rebinder.verify_current(daemon_epoch=9, master_epoch=4)

        self.assertEqual("VERIFIED", result["status"])
        self.assertEqual([("master", url)], driver.restore_calls)
        self.assertEqual(session, result["evidence"]["session"])
        self.assertEqual(1, len(persisted))


if __name__ == "__main__":
    unittest.main()
