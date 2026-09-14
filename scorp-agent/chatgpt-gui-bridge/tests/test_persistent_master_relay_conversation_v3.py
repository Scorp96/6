import asyncio
import pathlib
import tempfile
import unittest

from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from session_registry_v3 import SessionRegistryV3


class CapturingTransport:
    def __init__(self):
        self.submit_calls = []

    async def submit(self, *, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.submit_calls.append({
            "turn_id": turn_id,
            "actor_kind": actor_kind,
            "conversation_url": conversation_url,
        })
        url = conversation_url or f"https://chatgpt.com/c/{turn_id}"
        return {
            "status": "SUBMITTED",
            "turn_id": turn_id,
            "submission_id": f"submit-{turn_id}",
            "conversation_url": url,
            "handle": {
                "submission_id": f"submit-{turn_id}",
                "turn_id": turn_id,
                "conversation_url": url,
            },
        }

    async def poll(self, turn_id, *, timeout_seconds=1800):
        return {"status": "PENDING", "turn_id": turn_id}


class PersistentMasterRelayConversationV3Tests(unittest.TestCase):
    def _master_turn(self, session_id="a-window-new", turn_id="resume-a-new"):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": turn_id,
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": session_id,
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {"event": "RESUME_MASTER"},
        }

    def _worker_turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "worker-new",
            "project_id": "p",
            "state_version": 0,
            "actor_kind": "WORKER",
            "actor_id": "worker-001",
            "session_id": "worker-001-session-new",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "objective_sha256": "1" * 64,
            "payload": {"event": "ASSIGNMENT", "assignment": {"task": "bounded work"}},
        }

    def test_new_master_session_submits_into_existing_master_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            sessions = SessionRegistryV3(root / "sessions.json")
            canonical = "https://chatgpt.com/c/persistent-master-a"
            sessions.record_session(
                "p", "a-window-old", "MASTER", "A", "resume-a-old", canonical
            )
            transport = CapturingTransport()
            relay = ParallelMasterWorkerRelayV3(root, sessions, transport, max_inflight=1)
            relay._write_child(self._master_turn())

            result = asyncio.run(relay.run_tick())

            self.assertEqual(1, result["submitted"])
            self.assertEqual(1, len(transport.submit_calls))
            self.assertEqual("MASTER", transport.submit_calls[0]["actor_kind"])
            self.assertEqual(canonical, transport.submit_calls[0]["conversation_url"])

    def test_new_worker_session_does_not_inherit_master_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            sessions = SessionRegistryV3(root / "sessions.json")
            sessions.record_session(
                "p", "a-window-old", "MASTER", "A", "resume-a-old",
                "https://chatgpt.com/c/persistent-master-a",
            )
            transport = CapturingTransport()
            relay = ParallelMasterWorkerRelayV3(root, sessions, transport, max_inflight=1)
            relay._write_child(self._worker_turn())

            result = asyncio.run(relay.run_tick())

            self.assertEqual(1, result["submitted"])
            self.assertEqual("WORKER", transport.submit_calls[0]["actor_kind"])
            self.assertIsNone(transport.submit_calls[0]["conversation_url"])


if __name__ == "__main__":
    unittest.main()
