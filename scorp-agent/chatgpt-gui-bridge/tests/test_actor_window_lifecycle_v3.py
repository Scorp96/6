import asyncio
import base64
import json
import unittest

from actor_gui_backend_v3 import ActorGuiBackendV3
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3


def actor_line(turn_id, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"SCORP_GUI_ACTOR_V3::{turn_id}::{token}"


class LifecycleDriver:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.poll_calls = []
        self.close_calls = []

    async def snapshot_conversation(self, conversation_url, *, window_handle=None):
        self.poll_calls.append((conversation_url, window_handle))
        return self.snapshot

    async def close_conversation_window(self, window_handle):
        self.close_calls.append(window_handle)
        return True


class ActorWindowLifecycleV3Tests(unittest.TestCase):
    def test_pending_actor_keeps_hwnd_open(self):
        url = "https://chatgpt.com/c/worker-one"
        driver = LifecycleDriver("Focused Window: Chrome\nStop generating\nTURN_ID=turn-1")
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "actor_kind": "WORKER", "conversation_url": url, "window_handle": 424242},
            turn_id="turn-1", actor_kind="WORKER", timeout_seconds=10,
        ))
        self.assertEqual({"status": "PENDING"}, result)
        self.assertEqual([], driver.close_calls)

    def test_completed_actor_closes_its_exact_hwnd_after_parse(self):
        url = "https://chatgpt.com/c/worker-one"
        line = actor_line("turn-1", {"kind": "HANDOFF", "evidence": ["ok"]})
        driver = LifecycleDriver(f"Focused Window: Chrome\n{line}\n")
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "actor_kind": "WORKER", "conversation_url": url, "window_handle": 424242},
            turn_id="turn-1", actor_kind="WORKER", timeout_seconds=10,
        ))
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("HANDOFF", result["response"]["kind"])
        self.assertEqual([424242], driver.close_calls)

    def test_driver_close_conversation_window_uses_exact_hwnd_and_fails_closed(self):
        calls = []
        driver = WindowsMcpActorDriverV3(
            close_window_handle_fn=lambda hwnd: calls.append(hwnd) or True,
        )
        self.assertTrue(asyncio.run(driver.close_conversation_window(424242)))
        self.assertEqual([424242], calls)

        failing = WindowsMcpActorDriverV3(
            close_window_handle_fn=lambda hwnd: False,
        )
        with self.assertRaisesRegex(RuntimeError, "ACTOR_GUI_WINDOW_CLOSE_FAILED"):
            asyncio.run(failing.close_conversation_window(424242))

        with self.assertRaisesRegex(ValueError, "ACTOR_GUI_WINDOW_HANDLE_INVALID"):
            asyncio.run(driver.close_conversation_window(0))

    def test_stale_persisted_hwnd_falls_back_to_conversation_url(self):
        url = "https://chatgpt.com/c/worker-one"
        line = actor_line("turn-1", {"kind": "HANDOFF", "evidence": ["ok"]})

        class StaleHandleDriver:
            def __init__(self):
                self.poll_calls = []

            async def snapshot_conversation(self, conversation_url, *, window_handle=None):
                self.poll_calls.append((conversation_url, window_handle))
                if window_handle is not None:
                    raise RuntimeError("ACTOR_GUI_WINDOW_FOCUS_FAILED")
                return f"Focused Window: Chrome\n{line}\n"

        driver = StaleHandleDriver()
        backend = ActorGuiBackendV3(driver)
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "actor_kind": "WORKER", "conversation_url": url, "window_handle": 424242},
            turn_id="turn-1", actor_kind="WORKER", timeout_seconds=10,
        ))
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual([(url, 424242), (url, None)], driver.poll_calls)
    def test_completed_legacy_handle_without_hwnd_does_not_require_close_api(self):
        class LegacyDriver:
            async def snapshot_conversation(self, conversation_url):
                line = actor_line("turn-1", {"kind": "HANDOFF", "evidence": ["ok"]})
                return f"Focused Window: Chrome\n{line}\n"
        url = "https://chatgpt.com/c/worker-one"
        backend = ActorGuiBackendV3(LegacyDriver())
        result = asyncio.run(backend.poll(
            {"submission_id": "actor-submit-1", "turn_id": "turn-1", "actor_kind": "WORKER", "conversation_url": url},
            turn_id="turn-1", actor_kind="WORKER", timeout_seconds=10,
        ))
        self.assertEqual("COMPLETED", result["status"])


if __name__ == "__main__":
    unittest.main()