import asyncio
import json
import pathlib
import tempfile
import unittest

from master_worker_relay_v3 import MasterWorkerRelayV3
from session_registry_v3 import SessionRegistryV3


class OneShotGui:
    def __init__(self, response, url):
        self.response = response
        self.url = url
        self.calls = 0

    async def __call__(self, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("GUI_CALLED_MORE_THAN_ONCE_AFTER_DURABLE_RESPONSE")
        return self.response, "snapshot", self.url


def _master_turn():
    return {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": "master-journal-crash-001",
        "project_id": "project-journal",
        "state_version": 11,
        "actor_kind": "MASTER",
        "actor_id": "A",
        "session_id": "a-window-11",
        "root_objective_sha256": "g" * 64,
        "acceptance_sha256": "a" * 64,
        "payload": {"event": "RESUME_MASTER"},
    }


def _write_turn(root, turn):
    outbox = pathlib.Path(root) / "master-worker-v3-outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"{turn['turn_id']}.json").write_text(json.dumps(turn), encoding="utf-8")


class MasterWorkerRelayResponseJournalV3Tests(unittest.TestCase):
    def test_crash_after_gui_response_replays_journal_without_second_gui_call(self):
        with tempfile.TemporaryDirectory() as td:
            turn = _master_turn()
            _write_turn(td, turn)
            gui = OneShotGui({"kind": "WAIT"}, "https://chatgpt.com/c/a-journal")
            sessions = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            first = MasterWorkerRelayV3(td, sessions, gui)

            original_route = first._route

            def crash_after_response_is_durable(turn_value, response_value):
                raise RuntimeError("INJECTED_CRASH_AFTER_GUI_RESPONSE")

            first._route = crash_after_response_is_durable
            with self.assertRaisesRegex(RuntimeError, "INJECTED_CRASH_AFTER_GUI_RESPONSE"):
                asyncio.run(first.run_once())

            self.assertEqual(1, gui.calls)
            self.assertEqual(
                "https://chatgpt.com/c/a-journal",
                sessions.get_session_url("project-journal", "a-window-11"),
            )

            second = MasterWorkerRelayV3(td, sessions, gui)
            second._route = original_route
            result = asyncio.run(second.run_once())

            self.assertEqual("ROUTED", result["status"])
            self.assertEqual("master-journal-crash-001", result["turn_id"])
            self.assertEqual({"kind": "WAIT"}, result["response"])
            self.assertEqual(1, gui.calls)

    def test_journal_replay_keeps_original_response_even_if_gui_would_change(self):
        with tempfile.TemporaryDirectory() as td:
            turn = _master_turn()
            turn["turn_id"] = "master-journal-crash-002"
            _write_turn(td, turn)
            gui = OneShotGui(
                {"kind": "CONTINUE", "state_patch": {"next_exact_action": "original"}},
                "https://chatgpt.com/c/a-journal-2",
            )
            sessions = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            first = MasterWorkerRelayV3(td, sessions, gui)

            def crash_after_response_is_durable(turn_value, response_value):
                raise RuntimeError("INJECTED_CRASH_AFTER_GUI_RESPONSE")

            first._route = crash_after_response_is_durable
            with self.assertRaisesRegex(RuntimeError, "INJECTED_CRASH_AFTER_GUI_RESPONSE"):
                asyncio.run(first.run_once())

            second = MasterWorkerRelayV3(td, sessions, gui)
            result = asyncio.run(second.run_once())
            self.assertEqual(1, gui.calls)
            self.assertEqual("ROUTED", result["status"])
            self.assertEqual("CONTINUE", result["response"]["kind"])
            self.assertEqual("original", result["response"]["state_patch"]["next_exact_action"])


if __name__ == "__main__":
    unittest.main()
