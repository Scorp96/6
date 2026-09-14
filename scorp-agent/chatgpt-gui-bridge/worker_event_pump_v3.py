from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from worker_result_guard_v3 import WorkerResultGuardV3

TURN_PROTOCOL = "scorp.master-worker/turn-v3"


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("WORKER_EVENT_MASTER_TURN_INVALID")
    return value


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


class WorkerEventPumpV3:
    """Deterministically publishes pending worker events to the current A window.

    Publishing is not acknowledgement. The WorkerEvent remains PENDING until a
    later authoritative MasterStateTransition durably commits and ACKs it.
    """

    def __init__(self, project_state_store, master_lease_store, worker_event_queue, outbox_dir):
        self.project_state_store = project_state_store
        self.master_lease_store = master_lease_store
        self.worker_event_queue = worker_event_queue
        self.outbox = Path(outbox_dir)
        self.outbox.mkdir(parents=True, exist_ok=True)

    def _build_turn(self, state: dict, lease: dict, event: dict) -> dict:
        if event.get("project_id") != state.get("project_id"):
            raise ValueError("WORKER_EVENT_PROJECT_MISMATCH")
        if event.get("root_objective_sha256") != state.get("goal_contract_sha256"):
            raise ValueError("WORKER_EVENT_GOAL_CONTRACT_MISMATCH")
        if event.get("acceptance_sha256") != state.get("acceptance_sha256"):
            raise ValueError("WORKER_EVENT_ACCEPTANCE_CONTRACT_MISMATCH")
        try:
            source_version = int(event.get("source_state_version"))
        except Exception as exc:
            raise ValueError("WORKER_EVENT_SOURCE_STATE_VERSION_INVALID") from exc
        if source_version < 0 or source_version > int(state["state_version"]):
            raise ValueError("WORKER_EVENT_SOURCE_STATE_VERSION_INVALID")

        response_kind = str(event.get("response_kind") or "").upper()
        if response_kind not in {"HANDOFF", "BLOCKER"}:
            raise ValueError("WORKER_EVENT_RESPONSE_KIND_INVALID")
        worker_guard = WorkerResultGuardV3().evaluate(state, event)
        session_id = _text(lease.get("session_id"), "MASTER_SESSION_ID_MISSING")
        event_id = _text(event.get("event_id"), "WORKER_EVENT_ID_MISSING")
        material = {
            "project_id": state["project_id"],
            "state_version": state["state_version"],
            "master_session_id": session_id,
            "worker_event_id": event_id,
            "goal_contract_sha256": state["goal_contract_sha256"],
            "acceptance_sha256": state["acceptance_sha256"],
        }
        turn_id = "worker-event-master-" + _sha(material)[:24]
        return {
            "protocol_version": TURN_PROTOCOL,
            "turn_id": turn_id,
            "project_id": state["project_id"],
            "state_version": state["state_version"],
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": session_id,
            "root_objective_sha256": state["goal_contract_sha256"],
            "acceptance_sha256": state["acceptance_sha256"],
            "payload": {
                "event": "WORKER_" + response_kind,
                "worker_event_id": event_id,
                "source_worker_turn_id": event["source_turn_id"],
                "source_state_version": source_version,
                "worker_id": event["worker_id"],
                "objective_sha256": event["objective_sha256"],
                "worker_assignment": copy.deepcopy(event.get("assignment") or {}),
                "worker_guard": copy.deepcopy(worker_guard),
                "worker_response": copy.deepcopy(event["worker_response"]),
                "project_state_version": state["state_version"],
                "project_state_sha256": _sha(state),
                "project_state": copy.deepcopy(state),
            },
        }

    def run_once(self, project_id: str, *, now=None) -> dict:
        project_id = _text(project_id, "PROJECT_ID_EMPTY")
        state = self.project_state_store.load()
        if state.get("project_id") != project_id:
            raise ValueError("WORKER_EVENT_PROJECT_MISMATCH")

        status = state.get("status")
        if status in {"COMPLETE", "HARD_BLOCKED"}:
            return {"status": status, "project_id": project_id}
        if status != "ACTIVE":
            raise ValueError("PROJECT_STATE_STATUS_INVALID")

        pending = self.worker_event_queue.pending(project_id)
        if not pending:
            return {"status": "IDLE", "project_id": project_id}

        if not self.master_lease_store.is_active(project_id, now=now):
            return {
                "status": "MASTER_INACTIVE",
                "project_id": project_id,
                "event_id": pending[0]["event_id"],
            }
        lease = self.master_lease_store.load(project_id)
        if not isinstance(lease, dict) or lease.get("status") != "ACTIVE":
            raise ValueError("MASTER_WINDOW_NOT_ACTIVE")

        event = pending[0]
        turn = self._build_turn(state, lease, event)
        path = self.outbox / f"{turn['turn_id']}.json"
        if path.exists():
            existing = _read(path)
            if _sha(existing) != _sha(turn):
                raise ValueError("WORKER_EVENT_MASTER_TURN_CONFLICT")
            return {
                "status": "ALREADY_QUEUED",
                "project_id": project_id,
                "event_id": event["event_id"],
                "turn_id": turn["turn_id"],
            }

        _atomic(path, turn)
        return {
            "status": "QUEUED",
            "project_id": project_id,
            "event_id": event["event_id"],
            "turn_id": turn["turn_id"],
        }
