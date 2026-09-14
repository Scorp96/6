import json
import pathlib
import tempfile
import unittest

sys_path = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(sys_path))

from v4_browser_engine import build_v4_browser_engine


class FakeDriver:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.submits = []
        self.reconciles = []

    async def submit_prompt(self, **kwargs):
        self.submits.append(kwargs)
        return self.snapshot

    async def snapshot_conversation(self, conversation_url):
        self.reconciles.append(conversation_url)
        return self.snapshot


class V4BrowserEngineTests(unittest.TestCase):
    def intent(self):
        return {
            "intent_id": "intent-engine",
            "channel": "worker-1",
            "actor_id": "worker-1",
            "conversation_url": None,
            "payload_json": json.dumps({"prompt": "bounded task"}),
        }

    def test_parser_free_snapshot_is_submitted_but_not_captured(self):
        driver = FakeDriver(
            "Focused Window: Chrome\nhttps://chatgpt.com/c/engine-test\nassistant text"
        )
        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED", "channel": channel},
        )
        result = engine.submit(self.intent())
        self.assertEqual("SUBMITTED", result["status"])
        self.assertEqual("https://chatgpt.com/c/engine-test", result["conversation_url"])
        self.assertEqual("WORKER", driver.submits[0]["actor_kind"])
        self.assertEqual("intent-engine", driver.submits[0]["turn_id"])

    def test_parser_capture_and_reconcile_keep_url_identity(self):
        driver = FakeDriver("https://chatgpt.com/c/engine-test\nSCORP_RESULT")

        def parser(snapshot, intent_id):
            if "SCORP_RESULT" not in snapshot:
                return None
            return {"kind": "HANDOFF", "state": "DONE", "intent_id": intent_id}

        engine = build_v4_browser_engine(
            driver,
            auth_probe=lambda channel: {"status": "AUTHENTICATED"},
            response_parser=parser,
        )
        captured = engine.submit(self.intent())
        self.assertEqual("RESPONSE_CAPTURED", captured["status"])
        self.assertEqual("https://chatgpt.com/c/engine-test", captured["conversation_url"])
        reconciled = engine.reconcile({
            **self.intent(),
            "conversation_url": "https://chatgpt.com/c/engine-test",
        })
        self.assertEqual("RESPONSE_CAPTURED", reconciled["status"])
        self.assertEqual(["https://chatgpt.com/c/engine-test"], driver.reconciles)


if __name__ == "__main__":
    unittest.main()
