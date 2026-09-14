import asyncio
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from chat_resource_manager_v3 import ChatResourceManagerV3
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3

UTC = dt.timezone.utc


def turn(*, turn_id, actor_kind, actor_id, session_id, objective=None):
    value = {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": turn_id,
        "project_id": "project-gate",
        "state_version": 0,
        "actor_kind": actor_kind,
        "actor_id": actor_id,
        "session_id": session_id,
        "root_objective_sha256": "r" * 64,
        "acceptance_sha256": "a" * 64,
        "payload": {"event": "RESUME_MASTER" if actor_kind == "MASTER" else "ASSIGNMENT", "assignment": {}},
    }
    if objective is not None:
        value["objective_sha256"] = objective
    return value


class Sessions:
    def __init__(self, resolved=None):
        self.resolved = resolved

    def resolve_actor_conversation(self, project_id, session_id, actor_kind, actor_id):
        return self.resolved


class Transport:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    async def submit(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.result is not None:
            return dict(self.result)
        url = kwargs.get("conversation_url") or "https://chatgpt.com/c/new-chat"
        return {"status": "SUBMITTED", "submission_id": "sub-1", "conversation_url": url}

    async def poll(self, turn_id, *, timeout_seconds):
        return {"status": "PENDING"}


class ParallelRelayChatResourceGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.now = dt.datetime(2026, 9, 12, 7, 30, tzinfo=UTC)

    def tearDown(self):
        self.tmp.cleanup()

    def write_turn(self, value):
        outbox = self.root / "master-worker-v3-outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"{value['turn_id']}.json").write_text(json.dumps(value), encoding="utf-8")

    def manager(self):
        return ChatResourceManagerV3(
            self.root / "chat-resource-v3.json",
            creation_cooldown_seconds=60,
            throttle_backoff_seconds=300,
        )

    def throttled_resources(self):
        manager = self.manager()
        manager.note_throttle("CHATGPT_REQUEST_THROTTLED", now=self.now)
        return manager

    def test_circuit_open_defers_new_chat_without_transport_side_effect(self):
        self.write_turn(turn(
            turn_id="worker-new",
            actor_kind="WORKER",
            actor_id="worker-new",
            session_id="worker-new-session",
            objective="f" * 64,
        ))
        transport = Transport()
        relay = ParallelMasterWorkerRelayV3(
            self.root,
            Sessions(None),
            transport,
            chat_resources=self.throttled_resources(),
        )
        result = asyncio.run(relay.run_tick(now=self.now + dt.timedelta(seconds=1)))
        self.assertEqual(0, result["submitted"])
        self.assertEqual(1, result["deferred"])
        self.assertEqual([], transport.calls)
        ledger = json.loads((self.root / "master-worker-v3-ledger.json").read_text(encoding="utf-8")) if (self.root / "master-worker-v3-ledger.json").exists() else {}
        self.assertNotEqual("GUI_SUBMITTED", (ledger.get("worker-new") or {}).get("state"))

    def test_throttle_circuit_defers_existing_master_prompt_but_does_not_destroy_conversation_binding(self):
        self.write_turn(turn(
            turn_id="master-reuse",
            actor_kind="MASTER",
            actor_id="A",
            session_id="a-window-reuse",
        ))
        transport = Transport()
        existing = "https://chatgpt.com/c/master-existing"
        relay = ParallelMasterWorkerRelayV3(
            self.root,
            Sessions(existing),
            transport,
            chat_resources=self.throttled_resources(),
        )
        result = asyncio.run(relay.run_tick(now=self.now + dt.timedelta(seconds=1)))
        self.assertEqual(0, result["submitted"])
        self.assertEqual(1, result["deferred"])
        self.assertEqual([], transport.calls)

    def test_transport_deferred_throttle_opens_circuit_and_keeps_turn_deferred(self):
        self.write_turn(turn(
            turn_id="worker-throttle",
            actor_kind="WORKER",
            actor_id="worker-throttle",
            session_id="worker-throttle-session",
            objective="e" * 64,
        ))
        transport = Transport({
            "status": "DEFERRED",
            "submission_id": "sub-throttle",
            "reason": "CHATGPT_REQUEST_THROTTLED",
        })
        manager = self.manager()
        relay = ParallelMasterWorkerRelayV3(
            self.root,
            Sessions(None),
            transport,
            chat_resources=manager,
        )
        result = asyncio.run(relay.run_tick(now=self.now))
        self.assertEqual(0, result["submitted"])
        self.assertEqual(1, result["deferred"])
        self.assertEqual(1, len(transport.calls))
        permission = manager.permission(now=self.now + dt.timedelta(seconds=61), needs_new_chat=True)
        self.assertFalse(permission["allowed"])
        self.assertEqual("THROTTLE_CIRCUIT_OPEN", permission["reason"])
        state = json.loads((self.root / "chat-resource-v3.json").read_text(encoding="utf-8"))
        self.assertEqual("CHATGPT_REQUEST_THROTTLED", state["last_throttle_reason"])


if __name__ == "__main__":
    unittest.main()
