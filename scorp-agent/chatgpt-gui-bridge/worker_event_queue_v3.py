from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

PROTOCOL = "scorp.worker-event/v3"
TURN_PROTOCOL = "scorp.master-worker/turn-v3"
ALLOWED_RESPONSE_KINDS = {"HANDOFF", "BLOCKER"}


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def _read(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


class WorkerEventQueueV3:
    """Durable worker->A event queue independent of any master chat session."""

    def __init__(self, root):
        self.root = Path(root)
        self.events_dir = self.root / "events"
        self.ledger_path = self.root / "source-ledger.json"
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def _ledger(self) -> dict:
        value = _read(self.ledger_path, {}) or {}
        if not isinstance(value, dict):
            raise ValueError("WORKER_EVENT_LEDGER_INVALID")
        return value

    def _validate_turn(self, turn: dict) -> None:
        if not isinstance(turn, dict) or turn.get("protocol_version") != TURN_PROTOCOL:
            raise ValueError("WORKER_EVENT_TURN_PROTOCOL_INVALID")
        if str(turn.get("actor_kind") or "").upper() != "WORKER":
            raise ValueError("WORKER_EVENT_WORKER_ONLY")
        worker_id = _text(turn.get("actor_id"), "WORKER_EVENT_WORKER_ID_MISSING")
        if worker_id == "A" or not worker_id.startswith("worker-"):
            raise ValueError("WORKER_EVENT_WORKER_ID_INVALID")
        for key, error in (
            ("turn_id", "WORKER_EVENT_TURN_ID_MISSING"),
            ("project_id", "WORKER_EVENT_PROJECT_ID_MISSING"),
            ("objective_sha256", "WORKER_EVENT_OBJECTIVE_MISSING"),
            ("root_objective_sha256", "WORKER_EVENT_ROOT_HASH_MISSING"),
            ("acceptance_sha256", "WORKER_EVENT_ACCEPTANCE_HASH_MISSING"),
        ):
            _text(turn.get(key), error)
        try:
            version = int(turn.get("state_version"))
        except Exception as exc:
            raise ValueError("WORKER_EVENT_STATE_VERSION_INVALID") from exc
        if version < 0 or version != turn.get("state_version"):
            raise ValueError("WORKER_EVENT_STATE_VERSION_INVALID")

    def _normalize_response(self, response: dict) -> dict:
        if not isinstance(response, dict):
            raise ValueError("WORKER_EVENT_RESPONSE_INVALID")
        kind = str(response.get("kind") or "").upper()
        if kind not in ALLOWED_RESPONSE_KINDS:
            raise ValueError("WORKER_EVENT_RESPONSE_KIND_INVALID")
        normalized = copy.deepcopy(response)
        normalized["kind"] = kind
        return normalized

    def _event(self, turn: dict, response: dict) -> dict:
        payload = turn.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("WORKER_EVENT_TURN_PAYLOAD_INVALID")
        assignment = payload.get("assignment") or {}
        if not isinstance(assignment, dict):
            raise ValueError("WORKER_EVENT_ASSIGNMENT_INVALID")
        material = {
            "source_turn_id": turn["turn_id"],
            "project_id": turn["project_id"],
            "source_state_version": turn["state_version"],
            "worker_id": turn["actor_id"],
            "objective_sha256": turn["objective_sha256"],
            "root_objective_sha256": turn["root_objective_sha256"],
            "acceptance_sha256": turn["acceptance_sha256"],
            "assignment": copy.deepcopy(assignment),
            "worker_response": response,
        }
        event_id = "worker-event-" + _sha(material)[:24]
        return {
            "protocol_version": PROTOCOL,
            "event_id": event_id,
            "state": "PENDING",
            **material,
            "response_kind": response["kind"],
        }

    def enqueue(self, turn: dict, response: dict) -> dict:
        self._validate_turn(turn)
        normalized = self._normalize_response(response)
        event = self._event(turn, normalized)
        source_turn_id = turn["turn_id"]
        event_sha = _sha(event)
        ledger = self._ledger()
        old = ledger.get(source_turn_id)
        if isinstance(old, dict):
            if old.get("event_id") != event["event_id"] or old.get("event_sha256") != event_sha:
                raise ValueError("WORKER_EVENT_SOURCE_CONFLICT")
            persisted = self.get(event["event_id"])
            if _sha({**persisted, "state": "PENDING"} if persisted.get("state") == "ACKED" else persisted) != event_sha and persisted.get("state") != "ACKED":
                raise ValueError("WORKER_EVENT_PERSISTED_CONFLICT")
            return {"status": "ALREADY_QUEUED", "event_id": event["event_id"]}

        event_path = self.events_dir / f"{event['event_id']}.json"
        if event_path.exists():
            persisted = _read(event_path)
            if not isinstance(persisted, dict) or _sha(persisted) != event_sha:
                raise ValueError("WORKER_EVENT_PERSISTED_CONFLICT")
        else:
            _atomic(event_path, event)

        ledger[source_turn_id] = {
            "event_id": event["event_id"],
            "event_sha256": event_sha,
        }
        _atomic(self.ledger_path, ledger)
        return {"status": "QUEUED", "event_id": event["event_id"]}

    def get(self, event_id: str) -> dict:
        event_id = _text(event_id, "WORKER_EVENT_ID_MISSING")
        value = _read(self.events_dir / f"{event_id}.json")
        if not isinstance(value, dict):
            raise ValueError("WORKER_EVENT_NOT_FOUND")
        if value.get("protocol_version") != PROTOCOL or value.get("event_id") != event_id:
            raise ValueError("WORKER_EVENT_INVALID")
        return copy.deepcopy(value)

    def pending(self, project_id: str) -> list[dict]:
        project_id = _text(project_id, "WORKER_EVENT_PROJECT_ID_MISSING")
        found = []
        for path in sorted(self.events_dir.glob("*.json")):
            value = _read(path)
            if not isinstance(value, dict) or value.get("protocol_version") != PROTOCOL:
                raise ValueError("WORKER_EVENT_INVALID")
            if value.get("project_id") == project_id and value.get("state") == "PENDING":
                found.append(copy.deepcopy(value))
        found.sort(key=lambda item: item["event_id"])
        return found

    def acknowledge(self, event_id: str, master_transition_id: str) -> dict:
        event_id = _text(event_id, "WORKER_EVENT_ID_MISSING")
        master_transition_id = _text(master_transition_id, "WORKER_EVENT_ACK_ID_MISSING")
        path = self.events_dir / f"{event_id}.json"
        event = self.get(event_id)
        if event.get("state") == "ACKED":
            if event.get("ack_transition_id") != master_transition_id:
                raise ValueError("WORKER_EVENT_ACK_CONFLICT")
            return event
        if event.get("state") != "PENDING":
            raise ValueError("WORKER_EVENT_STATE_INVALID")
        event["state"] = "ACKED"
        event["ack_transition_id"] = master_transition_id
        _atomic(path, event)
        return copy.deepcopy(event)
