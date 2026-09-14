from __future__ import annotations

import asyncio
import pathlib
import tempfile
import unittest


class ReadOnlyPhysicalRebindTests(unittest.TestCase):
    def _store(self, root: pathlib.Path):
        from master_a_dynamic_v4.state_store import StateStore

        store = StateStore(root / "state.sqlite3", allowed_roots=[root])
        store.create_contract(
            "rebind-project",
            root_contract={"objective": "read-only rebind"},
            acceptance_contract={"required": ["AC07"]},
        )
        return store

    def test_rebind_reads_existing_chat_and_persists_evidence_without_send(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter
        from v4_physical_rebind import ReadOnlyBrowserRebinder

        class Driver:
            def __init__(self):
                self.snapshots = []

            async def snapshot_conversation(self, url):
                self.snapshots.append(url)
                return f"Focused Window: Chrome\\n{url}\\nexisting content"

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = self._store(root)
            try:
                adapter = BrowserAdapter(store, object())
                adapter.rebind(
                    "rebind-project",
                    "master",
                    actor_id="A",
                    conversation_url="https://chatgpt.com/c/existing",
                    predecessor_url=None,
                    reason="INITIAL_BINDING",
                    evidence={"source": "fixture"},
                )
                driver = Driver()
                rebinder = ReadOnlyBrowserRebinder(
                    store=store,
                    browser_adapter=adapter,
                    driver=driver,
                    auth_probe=lambda _channel: {"status": "AUTHENTICATED"},
                    project_id="rebind-project",
                )
                result = rebinder({"master_epoch": 3})
                self.assertEqual("REBOUND", result["status"])
                self.assertEqual(["https://chatgpt.com/c/existing"], driver.snapshots)
                self.assertEqual(
                    "READ_ONLY_PHYSICAL_REBIND",
                    result["evidence"]["source"],
                )
                self.assertEqual(
                    "https://chatgpt.com/c/existing",
                    store.get_browser_binding("rebind-project", "master")["conversation_url"],
                )
            finally:
                store.close()

    def test_authentication_block_stops_before_snapshot(self):
        from master_a_dynamic_v4.browser_adapter import BrowserAdapter
        from v4_physical_rebind import PhysicalRebindError, ReadOnlyBrowserRebinder

        class Driver:
            async def snapshot_conversation(self, _url):
                raise AssertionError("snapshot must not run after auth block")

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = self._store(root)
            try:
                adapter = BrowserAdapter(store, object())
                adapter.rebind(
                    "rebind-project",
                    "master",
                    actor_id="A",
                    conversation_url="https://chatgpt.com/c/existing",
                    predecessor_url=None,
                    reason="INITIAL_BINDING",
                    evidence={"source": "fixture"},
                )
                rebinder = ReadOnlyBrowserRebinder(
                    store=store,
                    browser_adapter=adapter,
                    driver=Driver(),
                    auth_probe=lambda _channel: {"status": "AUTHENTICATION_REQUIRED"},
                    project_id="rebind-project",
                )
                with self.assertRaisesRegex(PhysicalRebindError, "AUTH_BLOCKED"):
                    rebinder({"master_epoch": 3})
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
