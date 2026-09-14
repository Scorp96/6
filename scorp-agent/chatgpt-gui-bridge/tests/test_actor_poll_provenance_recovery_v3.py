import asyncio
import base64
import json
import unittest

from actor_gui_backend_v3 import ActorGuiBackendV3


def actor_line(turn_id, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"SCORP_GUI_ACTOR_V3::{turn_id}::{token}"


class ProvenanceDriver:
    def __init__(self, fallback_snapshot, *, first_error="ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH"):
        self.fallback_snapshot = fallback_snapshot
        self.first_error = first_error
        self.snapshot_calls = []
        self.close_calls = []

    async def snapshot_conversation(self, conversation_url, *, window_handle=None):
        self.snapshot_calls.append((conversation_url, window_handle))
        if window_handle is not None:
            raise ValueError(self.first_error)
        return self.fallback_snapshot

    async def close_conversation_window(self, window_handle):
        self.close_calls.append(window_handle)
        return True


class GroupedProvenanceDriver(ProvenanceDriver):
    def __init__(self, fallback_snapshot, *, mixed=False):
        super().__init__(fallback_snapshot)
        self.mixed = mixed

    async def snapshot_conversation(self, conversation_url, *, window_handle=None):
        self.snapshot_calls.append((conversation_url, window_handle))
        if window_handle is not None:
            leaves = [ValueError("ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH")]
            if self.mixed:
                leaves.append(RuntimeError("UNRELATED_MCP_FAILURE"))
            raise ExceptionGroup("outer", [ExceptionGroup("inner", leaves)])
        return self.fallback_snapshot


class ActorPollProvenanceRecoveryV3Tests(unittest.TestCase):
    def test_mismatched_persisted_hwnd_falls_back_to_canonical_url_without_closing_stale_hwnd(self):
        url = "https://chatgpt.com/c/worker-one"
        line = actor_line("turn-1", {"kind": "HANDOFF", "summary": "done"})
        driver = ProvenanceDriver(f"Focused Window: Chrome\n{line}\n")
        backend = ActorGuiBackendV3(driver)

        result = asyncio.run(backend.poll(
            {
                "submission_id": "actor-submit-1",
                "turn_id": "turn-1",
                "actor_kind": "WORKER",
                "conversation_url": url,
                "window_handle": 77,
            },
            turn_id="turn-1",
            actor_kind="WORKER",
            timeout_seconds=10,
        ))

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("HANDOFF", result["response"]["kind"])
        self.assertEqual([(url, 77), (url, None)], driver.snapshot_calls)
        self.assertEqual([], driver.close_calls)

    def test_mismatched_persisted_hwnd_fallback_can_remain_pending_without_closing_stale_hwnd(self):
        url = "https://chatgpt.com/c/worker-one"
        driver = ProvenanceDriver("Focused Window: Chrome\nNo actor response yet\n")
        backend = ActorGuiBackendV3(driver)

        result = asyncio.run(backend.poll(
            {
                "submission_id": "actor-submit-1",
                "turn_id": "turn-1",
                "actor_kind": "WORKER",
                "conversation_url": url,
                "window_handle": 77,
            },
            turn_id="turn-1",
            actor_kind="WORKER",
            timeout_seconds=10,
        ))

        self.assertEqual({"status": "PENDING"}, result)
        self.assertEqual([(url, 77), (url, None)], driver.snapshot_calls)
        self.assertEqual([], driver.close_calls)

    def test_nested_exception_group_containing_only_provenance_mismatch_is_recoverable(self):
        url = "https://chatgpt.com/c/worker-one"
        line = actor_line("turn-1", {"kind": "HANDOFF", "summary": "done"})
        driver = GroupedProvenanceDriver(f"Focused Window: Chrome\n{line}\n")
        backend = ActorGuiBackendV3(driver)

        result = asyncio.run(backend.poll(
            {
                "submission_id": "actor-submit-1",
                "turn_id": "turn-1",
                "actor_kind": "WORKER",
                "conversation_url": url,
                "window_handle": 77,
            },
            turn_id="turn-1",
            actor_kind="WORKER",
            timeout_seconds=10,
        ))

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual([(url, 77), (url, None)], driver.snapshot_calls)
        self.assertEqual([], driver.close_calls)

    def test_mixed_exception_group_still_fails_closed(self):
        url = "https://chatgpt.com/c/worker-one"
        driver = GroupedProvenanceDriver("", mixed=True)
        backend = ActorGuiBackendV3(driver)

        with self.assertRaises(ExceptionGroup):
            asyncio.run(backend.poll(
                {
                    "submission_id": "actor-submit-1",
                    "turn_id": "turn-1",
                    "actor_kind": "WORKER",
                    "conversation_url": url,
                    "window_handle": 77,
                },
                turn_id="turn-1",
                actor_kind="WORKER",
                timeout_seconds=10,
            ))

        self.assertEqual([(url, 77)], driver.snapshot_calls)
        self.assertEqual([], driver.close_calls)

    def test_non_provenance_poll_error_still_fails_closed(self):
        url = "https://chatgpt.com/c/worker-one"
        driver = ProvenanceDriver("", first_error="SOME_OTHER_FAILURE")
        backend = ActorGuiBackendV3(driver)

        with self.assertRaisesRegex(ValueError, "SOME_OTHER_FAILURE"):
            asyncio.run(backend.poll(
                {
                    "submission_id": "actor-submit-1",
                    "turn_id": "turn-1",
                    "actor_kind": "WORKER",
                    "conversation_url": url,
                    "window_handle": 77,
                },
                turn_id="turn-1",
                actor_kind="WORKER",
                timeout_seconds=10,
            ))

        self.assertEqual([(url, 77)], driver.snapshot_calls)
        self.assertEqual([], driver.close_calls)


if __name__ == "__main__":
    unittest.main()
