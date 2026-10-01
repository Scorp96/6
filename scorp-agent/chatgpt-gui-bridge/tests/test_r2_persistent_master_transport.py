from __future__ import annotations

import asyncio
import json
import pathlib
import tempfile
import unittest

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from gui_engine import ChatGptGuiEngine
from v4_physical_rebind import ReadOnlyBrowserRebinder


class R2PersistentMasterTransportTests(unittest.TestCase):
    def test_installer_allows_runtime_and_watchdog_to_survive_battery_transition(self):
        installer = pathlib.Path(__file__).resolve().parents[1] / "install-v4-daemon.ps1"
        text = installer.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("-AllowStartIfOnBatteries"), 2)
        self.assertGreaterEqual(text.count("-DontStopIfGoingOnBatteries"), 2)

    def test_gui_engine_targets_durable_master_transport_when_observed_url_is_not_yet_set(self):
        calls = []
        target = "https://chatgpt.com/c/master-r2"

        async def run_turn(
            prompt,
            request_id,
            *,
            timeout_seconds,
            conversation_url=None,
            actor_kind="MASTER",
        ):
            calls.append(
                {
                    "prompt": prompt,
                    "request_id": request_id,
                    "timeout_seconds": timeout_seconds,
                    "conversation_url": conversation_url,
                    "actor_kind": actor_kind,
                }
            )
            return (
                {"master_decision_version": 1},
                "Focused Window: Chrome\n" + target + "\n{}",
                target,
            )

        engine = ChatGptGuiEngine(
            run_turn,
            auth_probe=lambda _channel: {"status": "AUTHENTICATED"},
            timeout_seconds=3,
        )
        result = engine.submit(
            {
                "intent_id": "master-reasoning-r2",
                "actor_id": "A",
                "channel": "master",
                "conversation_url": None,
                "payload_json": json.dumps(
                    {
                        "prompt": "hello",
                        "transport_binding": {
                            "channel": "master",
                            "actor_id": "A",
                            "conversation_url": target,
                            "generation": 4,
                        },
                    }
                ),
            }
        )
        self.assertEqual(target, calls[0]["conversation_url"])
        self.assertEqual("MASTER", calls[0]["actor_kind"])
        self.assertEqual(target, result["conversation_url"])

    def test_same_canonical_master_url_reuses_session_after_driver_reload(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / "driver.json"
            url = "https://chatgpt.com/c/master-r2"
            first_driver = ChromeUseActorDriverV3(object(), state_path)
            first = first_driver.bind_turn(
                "master-turn-one",
                url,
                actor_kind="MASTER",
            )

            reloaded = ChromeUseActorDriverV3(object(), state_path)
            second = reloaded.bind_turn(
                "master-turn-two",
                url,
                actor_kind="MASTER",
            )

            self.assertEqual(first, second)
            self.assertTrue(first.startswith("scorp-p0-conv-"))

    def test_physical_health_evidence_preserves_session_identity(self):
        url = "https://chatgpt.com/c/master-r2"
        session = "scorp-p0-conv-r2"

        class Store:
            def get_browser_binding(self, _project_id, _channel):
                return {
                    "project_id": "p",
                    "channel": "master",
                    "actor_id": "A",
                    "conversation_url": url,
                    "generation": 3,
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
            browser_adapter=object(),
            driver=Driver(),
            auth_probe=lambda _channel: {"status": "AUTHENTICATED"},
            project_id="p",
        )
        result = rebinder.health_probe()
        self.assertEqual("HEALTHY", result["status"])
        self.assertEqual(session, result["evidence"]["session"])
        self.assertEqual(3, result["evidence"]["binding_generation"])


if __name__ == "__main__":
    unittest.main()
