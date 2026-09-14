import asyncio
import base64
import json
import unittest

from actor_gui_backend_v3 import ActorGuiBackendV3


def actor_line(turn_id, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"SCORP_GUI_ACTOR_V3::{turn_id}::{token}"


class FakeDriver:
    def __init__(self):
        self.submit_calls = []
        self.snapshot_calls = []
        self.discover_calls = []
        self.submit_snapshots = []
        self.snapshots = {}
        self.discovered = []

    async def submit_prompt(self, *, prompt, turn_id, actor_kind, conversation_url):
        self.submit_calls.append((prompt, turn_id, actor_kind, conversation_url))
        if self.submit_snapshots:
            return self.submit_snapshots.pop(0)
        return ""

    async def snapshot_conversation(self, conversation_url):
        self.snapshot_calls.append(conversation_url)
        value = self.snapshots.get(conversation_url, "")
        if isinstance(value, list):
            if not value:
                return ""
            return value.pop(0)
        return value

    async def discover_turn_conversations(self, turn_id):
        self.discover_calls.append(turn_id)
        return list(self.discovered)


class ActorGuiBackendV3Tests(unittest.TestCase):
    def test_submit_returns_after_canonical_url_without_waiting_for_actor_response(self):
        driver = FakeDriver()
        driver.submit_snapshots = ["Focused Window: Chrome\nhttps://chatgpt.com/c/worker-one\nStop generating"]
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.submit(
            "actor-submit-1",
            prompt="PROMPT",
            turn_id="turn-1",
            actor_kind="WORKER",
            conversation_url=None,
            timeout_seconds=1800,
        ))
        self.assertEqual("actor-submit-1", result["submission_id"])
        self.assertEqual("https://chatgpt.com/c/worker-one", result["conversation_url"])
        self.assertEqual([], driver.snapshot_calls)
        self.assertEqual(1, len(driver.submit_calls))

    def test_poll_returns_pending_while_generation_is_active(self):
        driver = FakeDriver()
        url = "https://chatgpt.com/c/worker-one"
        driver.snapshots[url] = "Focused Window: Chrome\nStop generating\nTURN_ID=turn-1"
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "conversation_url": url},
            turn_id="turn-1",
            actor_kind="WORKER",
            timeout_seconds=10,
        ))
        self.assertEqual({"status": "PENDING"}, result)

    def test_poll_returns_completed_actor_response_when_terminal_marker_exists(self):
        driver = FakeDriver()
        url = "https://chatgpt.com/c/worker-one"
        line = actor_line("turn-1", {"kind": "HANDOFF", "evidence": ["ok"]})
        driver.snapshots[url] = f"Focused Window: Chrome\n{line}\n"
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "conversation_url": url},
            turn_id="turn-1",
            actor_kind="WORKER",
            timeout_seconds=10,
        ))
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("HANDOFF", result["response"]["kind"])
        self.assertEqual(url, result["conversation_url"])
        self.assertIn(line, result["snapshot"])

    def test_poll_terminal_without_marker_remains_pending_instead_of_parse_error(self):
        driver = FakeDriver()
        url = "https://chatgpt.com/c/worker-one"
        driver.snapshots[url] = "Focused Window: Chrome\nNo actor response yet\n"
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "conversation_url": url},
            turn_id="turn-1",
            actor_kind="WORKER",
            timeout_seconds=10,
        ))
        self.assertEqual("PENDING", result["status"])

    def test_poll_partial_actor_marker_remains_pending_until_token_is_complete(self):
        driver = FakeDriver()
        url = "https://chatgpt.com/c/master-one"
        complete = actor_line("turn-1", {"kind": "WAIT", "state_patch": {}})
        partial = complete[:-7]
        driver.snapshots[url] = f"Focused Window: Chrome\n{partial}\n"
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "conversation_url": url},
            turn_id="turn-1",
            actor_kind="MASTER",
            timeout_seconds=10,
        ))
        self.assertEqual({"status": "PENDING"}, result)

    def test_recover_returns_unique_discovered_conversation_without_resubmitting(self):
        driver = FakeDriver()
        driver.discovered = [
            {"conversation_url": "https://chatgpt.com/c/recovered-one", "snapshot": "TURN_ID=turn-1"}
        ]
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.recover("actor-submit-1", turn_id="turn-1", actor_kind="WORKER"))
        self.assertEqual("actor-submit-1", result["submission_id"])
        self.assertEqual("https://chatgpt.com/c/recovered-one", result["conversation_url"])
        self.assertEqual([], driver.submit_calls)

    def test_recover_zero_returns_none_and_multiple_fails_closed(self):
        driver = FakeDriver()
        backend = ActorGuiBackendV3(driver)
        self.assertIsNone(asyncio.run(backend.recover("actor-submit-1", turn_id="turn-1", actor_kind="WORKER")))
        driver.discovered = [
            {"conversation_url": "https://chatgpt.com/c/one", "snapshot": "TURN_ID=turn-1"},
            {"conversation_url": "https://chatgpt.com/c/two", "snapshot": "TURN_ID=turn-1"},
        ]
        with self.assertRaisesRegex(ValueError, "ACTOR_GUI_RECOVERY_AMBIGUOUS"):
            asyncio.run(backend.recover("actor-submit-1", turn_id="turn-1", actor_kind="WORKER"))

    def test_poll_legacy_windows_handle_falls_back_to_canonical_url_for_chrome_use(self):
        class ChromeUseLikeDriver(FakeDriver):
            async def snapshot_conversation(self, conversation_url, *, window_handle=None):
                if window_handle is not None:
                    raise ValueError("CHROME_USE_WINDOW_HANDLE_UNSUPPORTED")
                return await super().snapshot_conversation(conversation_url)

        driver = ChromeUseLikeDriver()
        url = "https://chatgpt.com/c/worker-one"
        line = actor_line("turn-1", {"kind": "HANDOFF", "evidence": ["legacy-recovered"]})
        driver.snapshots[url] = f"Focused Window: Chrome\n{line}\n"
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {
                "submission_id": "actor-submit-1",
                "turn_id": "turn-1",
                "actor_kind": "WORKER",
                "conversation_url": url,
                "window_handle": 2361378,
            },
            turn_id="turn-1",
            actor_kind="WORKER",
            timeout_seconds=10,
        ))
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("HANDOFF", result["response"]["kind"])
        self.assertEqual([url], driver.snapshot_calls)

    def test_handle_turn_identity_mismatch_fails_closed_before_snapshot(self):
        driver = FakeDriver()
        backend = ActorGuiBackendV3(driver)
        with self.assertRaisesRegex(ValueError, "ACTOR_GUI_HANDLE_TURN_CONFLICT"):
            asyncio.run(backend.poll(
                {"submission_id": "actor-submit-1", "turn_id": "other", "conversation_url": "https://chatgpt.com/c/one"},
                turn_id="turn-1",
                actor_kind="WORKER",
                timeout_seconds=10,
            ))
        self.assertEqual([], driver.snapshot_calls)


if __name__ == "__main__":
    unittest.main()
