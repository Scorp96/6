from __future__ import annotations

import asyncio
import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from pinned_master_presence_preflight_v4 import check_persisted_master_readonly

URL = "https://chatgpt.com/c/pinned-owned-master"
SESSION = "durable-private-master-session"


class FakeCLI:
    def __init__(self, *, tabs=None, sessions=None):
        self.calls = []
        self.sessions = sessions if sessions is not None else {
            "ok": True, "sessions": [{"name": SESSION, "owner": "fixture", "pid": 1}],
        }
        self.tabs = tabs if tabs is not None else {
            "success": True, "data": {
                "full": True, "tabs": [{
                    "url": URL, "active": True, "relayAttached": True,
                    "type": "page", "ownership": "unknown",
                }],
            },
        }

    async def list_sessions_readonly(self, *, timeout_seconds=10):
        self.calls.append("sessions")
        return self.sessions

    async def list_tabs_readonly(self, session, *, timeout_seconds=10):
        self.calls.append("tabs")
        if session != SESSION:
            raise AssertionError("unexpected session selected")
        return self.tabs


class PinnedMasterPreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.project = self.root / "state-v3/active/project-state.json"
        self.registry = self.root / "state-v3/active/sessions-v3.json"
        self.driver = self.root / "state-v3/active/chrome-use-driver-v3.json"

    def store(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def fixtures(self):
        self.store(self.project, {"project_id": "test-project"})
        self.store(self.registry, {
            "master-conversation:test-project": {"conversation_url": URL},
        })
        self.store(self.driver, {
            "protocol_version": "scorp.driver/3",
            "turns": {},
            "conversations": {URL: {"session": SESSION}},
        })

    def probe(self, cli=None):
        return asyncio.run(check_persisted_master_readonly(
            self.root, cli=cli if cli is not None else FakeCLI(),
        ))

    def test_known_binding_live_tab_is_not_auth_or_completion(self):
        self.fixtures()
        cli = FakeCLI()
        before = [p.read_bytes() for p in (self.project, self.registry, self.driver)]
        result = self.probe(cli)
        self.assertEqual("PRESENCE_CANDIDATE_UNATTESTED", result.status)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.auth_verified)
        self.assertFalse(result.host_terminal_event_verified)
        self.assertEqual("NONE", result.production_writes)
        self.assertEqual(0, result.model_calls)
        self.assertEqual(["sessions", "tabs"], cli.calls)
        self.assertEqual(before, [p.read_bytes() for p in (
            self.project, self.registry, self.driver,
        )])
        self.assertNotIn(URL, str(result))
        self.assertNotIn(SESSION, str(result))

    def test_unrelated_only_online_session_cannot_be_adopted(self):
        self.fixtures()
        cli = FakeCLI(sessions={"ok": True, "sessions": [{"name": "unrelated-live"}]})
        result = self.probe(cli)
        self.assertEqual("PINNED_SESSION_MISSING", result.reason)
        self.assertEqual(["sessions"], cli.calls)

    def test_about_blank_never_claims_pinned_master(self):
        self.fixtures()
        cli = FakeCLI(tabs={
            "success": True, "data": {"full": True, "tabs": [{
                "url": "about:blank", "type": "page",
                "active": True, "relayAttached": True,
            }]},
        })
        result = self.probe(cli)
        self.assertEqual("PINNED_CHATGPT_TAB_ABSENT", result.reason)
        self.assertEqual(0, result.matching_tab_count)

    def test_missing_master_registry_does_not_call_browser(self):
        self.store(self.project, {"project_id": "test-project"})
        cli = FakeCLI()
        result = self.probe(cli)
        self.assertEqual("PERSISTED_MASTER_REGISTRY_UNAVAILABLE", result.reason)
        self.assertEqual([], cli.calls)

    def test_driver_without_pinned_session_does_not_fallback(self):
        self.fixtures()
        self.store(self.driver, {"conversations": {URL: {}}})
        cli = FakeCLI()
        self.assertEqual("PINNED_DRIVER_SESSION_UNAVAILABLE", self.probe(cli).reason)
        self.assertEqual([], cli.calls)

    def test_corrupt_or_oversize_registry_fails_closed(self):
        self.fixtures()
        self.registry.write_text("{not json", encoding="utf-8")
        self.assertEqual("PERSISTED_MASTER_REGISTRY_UNAVAILABLE", self.probe().reason)
        self.registry.write_bytes(b"x" * (1024*1024+1))
        self.assertEqual("PERSISTED_MASTER_REGISTRY_UNAVAILABLE", self.probe().reason)

    def test_no_local_files_are_created_when_roots_are_missing(self):
        cli = FakeCLI()
        result = self.probe(cli)
        self.assertEqual("PROJECT_ID_UNAVAILABLE", result.reason)
        self.assertFalse((self.root / "state-v3").exists())
        self.assertEqual([], cli.calls)


if __name__ == "__main__":
    unittest.main()
