import asyncio
import json
import pathlib
import tempfile
import unittest

from master_worker_relay_v3 import MasterWorkerRelayV3
from session_registry_v3 import SessionRegistryV3


class FakeGui:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800):
        self.calls.append((turn_id, actor_kind, conversation_url, prompt))
        response, url = self.responses.pop(0)
        return response, "snapshot", url


class MasterWorkerRelayV3Tests(unittest.TestCase):
    def _master_turn(self):
        return {
            "protocol_version": "scorp.master-worker/turn-v3",
            "turn_id": "master-turn-001",
            "project_id": "project-1",
            "state_version": 7,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": "a-window-7",
            "root_objective_sha256": "g" * 64,
            "acceptance_sha256": "a" * 64,
            "payload": {"event": "RESUME_MASTER"},
        }

    def _write_turn(self, root, turn):
        outbox = pathlib.Path(root) / "master-worker-v3-outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"{turn['turn_id']}.json").write_text(json.dumps(turn), encoding="utf-8")

    def test_master_dispatch_allocates_dynamic_workers_and_core_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            turn = self._master_turn()
            self._write_turn(td, turn)
            response = {
                "kind": "DISPATCH",
                "assignments": [
                    {"objective_sha256": "1" * 64, "payload": {"task": "github audit"}},
                    {"objective_sha256": "2" * 64, "payload": {"task": "api research"}},
                    {"objective_sha256": "3" * 64, "payload": {"task": "regression tests"}},
                ],
            }
            gui = FakeGui([(response, "https://chatgpt.com/c/a-111")])
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            runtime = MasterWorkerRelayV3(td, reg, gui)
            result = asyncio.run(runtime.run_once())
            self.assertEqual("ROUTED", result["status"])
            self.assertEqual(4, len(result["children"]))
            children = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((pathlib.Path(td) / "master-worker-v3-outbox").glob("*.json")) if p.stem != turn["turn_id"]]
            workers = [x for x in children if x["actor_kind"] == "WORKER"]
            masters = [x for x in children if x["actor_kind"] == "MASTER"]
            self.assertEqual(3, len(workers))
            self.assertEqual(1, len(masters))
            self.assertEqual(3, len({x["actor_id"] for x in workers}))
            self.assertTrue(all(x["actor_id"].startswith("worker-") for x in workers))
            self.assertTrue(all(x["session_id"].startswith(x["actor_id"] + "-session-") for x in workers))
            self.assertEqual("A", masters[0]["actor_id"])
            self.assertEqual("a-window-7", masters[0]["session_id"])
            self.assertEqual("CONTINUE_CORE_AFTER_DISPATCH", masters[0]["payload"]["event"])

    def test_same_master_session_url_is_reused_for_core_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            reg.record_session("project-1", "a-window-7", "MASTER", "A", "old", "https://chatgpt.com/c/a-111")
            turn = self._master_turn()
            turn["turn_id"] = "master-turn-002"
            self._write_turn(td, turn)
            gui = FakeGui([({"kind": "WAIT"}, "https://chatgpt.com/c/a-111")])
            result = asyncio.run(MasterWorkerRelayV3(td, reg, gui).run_once())
            self.assertEqual("https://chatgpt.com/c/a-111", gui.calls[0][2])
            self.assertEqual("ROUTED", result["status"])

    def test_worker_handoff_is_queued_without_binding_dead_master_session(self):
        with tempfile.TemporaryDirectory() as td:
            worker = {
                "protocol_version": "scorp.master-worker/turn-v3",
                "turn_id": "worker-turn-001",
                "project_id": "project-1",
                "state_version": 7,
                "actor_kind": "WORKER",
                "actor_id": "worker-abc123",
                "session_id": "worker-abc123-session-1",
                "root_objective_sha256": "g" * 64,
                "acceptance_sha256": "a" * 64,
                "objective_sha256": "1" * 64,
                "payload": {"event": "ASSIGNMENT", "controller_session_id": "dead-a-window-7"},
            }
            self._write_turn(td, worker)
            gui = FakeGui([({"kind": "HANDOFF", "evidence": ["ok"]}, "https://chatgpt.com/c/w-111")])
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            runtime = MasterWorkerRelayV3(td, reg, gui)
            result = asyncio.run(runtime.run_once())
            self.assertEqual("ROUTED", result["status"])
            self.assertEqual([], result["children"])
            pending = runtime.worker_events.pending("project-1")
            self.assertEqual(1, len(pending))
            self.assertEqual("worker-abc123", pending[0]["worker_id"])
            self.assertEqual("HANDOFF", pending[0]["response_kind"])
            self.assertNotIn("controller_session_id", pending[0])
            master_children = [json.loads(p.read_text(encoding="utf-8")) for p in (pathlib.Path(td) / "master-worker-v3-outbox").glob("*.json") if p.stem != worker["turn_id"]]
            self.assertEqual([], master_children)

    def test_replay_is_idempotent_and_conflicting_same_turn_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            turn = self._master_turn()
            self._write_turn(td, turn)
            gui = FakeGui([({"kind": "WAIT"}, "https://chatgpt.com/c/a-111")])
            reg = SessionRegistryV3(pathlib.Path(td) / "sessions.json")
            runtime = MasterWorkerRelayV3(td, reg, gui)
            first = asyncio.run(runtime.run_once())
            second = asyncio.run(runtime.run_once())
            self.assertEqual("ROUTED", first["status"])
            self.assertEqual("IDLE", second["status"])
            turn["payload"] = {"event": "CHANGED"}
            self._write_turn(td, turn)
            with self.assertRaisesRegex(ValueError, "ACTOR_TURN_CONFLICT"):
                asyncio.run(runtime.run_once())

    def test_dispatch_rejects_duplicate_objectives_and_excessive_worker_count(self):
        with tempfile.TemporaryDirectory() as td:
            turn = self._master_turn()
            self._write_turn(td, turn)
            dup = {"kind": "DISPATCH", "assignments": [{"objective_sha256": "1" * 64}, {"objective_sha256": "1" * 64}]}
            gui = FakeGui([(dup, "https://chatgpt.com/c/a-111")])
            with self.assertRaisesRegex(ValueError, "DISPATCH_OBJECTIVE_DUPLICATE"):
                asyncio.run(MasterWorkerRelayV3(td, SessionRegistryV3(pathlib.Path(td) / "sessions.json"), gui).run_once())

        with tempfile.TemporaryDirectory() as td:
            turn = self._master_turn()
            self._write_turn(td, turn)
            assignments = [{"objective_sha256": f"{i:064x}"} for i in range(9)]
            gui = FakeGui([({"kind": "DISPATCH", "assignments": assignments}, "https://chatgpt.com/c/a-111")])
            with self.assertRaisesRegex(ValueError, "DISPATCH_WORKER_LIMIT"):
                asyncio.run(MasterWorkerRelayV3(td, SessionRegistryV3(pathlib.Path(td) / "sessions.json"), gui).run_once())


if __name__ == "__main__":
    unittest.main()
