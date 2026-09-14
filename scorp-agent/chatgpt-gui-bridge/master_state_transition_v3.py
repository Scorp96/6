from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json

UTC = dt.timezone.utc
TURN_PROTOCOL = "scorp.master-worker/turn-v3"
ALLOWED_KINDS = {"CONTINUE", "DISPATCH", "DRAIN", "TERMINAL", "WAIT"}
TERMINAL_STATUS = {"COMPLETE", "HARD_BLOCKED"}
RESERVED_PATCH_FIELDS = {
    "protocol_version",
    "project_id",
    "master_identity",
    "state_version",
    "goal_contract_sha256",
    "acceptance_sha256",
    "status",
    "active_master_window",
    "active_workers",
    "last_master_transition_id",
}
MAX_DYNAMIC_WORKERS = 8


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _aware_utc(value: dt.datetime | None) -> dt.datetime:
    value = value or dt.datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("MASTER_TRANSITION_TIME_NAIVE")
    return value.astimezone(UTC)


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


class MasterStateTransitionV3:
    """Applies one authoritative A->PROJECT_STATE transition.

    The PROJECT_STATE CAS is persisted before operational side effects.
    Replaying the exact same transition repairs interrupted lease/worker-event
    side effects without incrementing state_version a second time.
    """

    def __init__(self, project_state_store, master_lease_store, worker_event_queue=None):
        self.project_state_store = project_state_store
        self.master_lease_store = master_lease_store
        self.worker_event_queue = worker_event_queue

    def _validate_turn_contract(self, turn: dict, state: dict) -> None:
        if not isinstance(turn, dict):
            raise ValueError("MASTER_TURN_NOT_OBJECT")
        if turn.get("protocol_version") != TURN_PROTOCOL:
            raise ValueError("MASTER_TURN_PROTOCOL_INVALID")
        if str(turn.get("actor_kind") or "").upper() != "MASTER":
            raise ValueError("PROJECT_STATE_MASTER_ONLY")
        if turn.get("actor_id") != "A":
            raise ValueError("MASTER_IDENTITY_INVALID")
        if turn.get("project_id") != state.get("project_id"):
            raise ValueError("MASTER_PROJECT_ID_MISMATCH")
        if turn.get("root_objective_sha256") != state.get("goal_contract_sha256"):
            raise ValueError("MASTER_GOAL_CONTRACT_MISMATCH")
        if turn.get("acceptance_sha256") != state.get("acceptance_sha256"):
            raise ValueError("MASTER_ACCEPTANCE_CONTRACT_MISMATCH")
        _text(turn.get("turn_id"), "MASTER_TURN_ID_MISSING")
        _text(turn.get("session_id"), "MASTER_SESSION_ID_MISSING")
        try:
            version = int(turn.get("state_version"))
        except Exception as exc:
            raise ValueError("MASTER_STATE_VERSION_INVALID") from exc
        if version < 0 or version != turn.get("state_version"):
            raise ValueError("MASTER_STATE_VERSION_INVALID")

    def _normalize_response(self, response: dict) -> tuple[str, dict]:
        if not isinstance(response, dict):
            raise ValueError("MASTER_RESPONSE_NOT_OBJECT")
        kind = str(response.get("kind") or "").upper()
        if kind not in ALLOWED_KINDS:
            raise ValueError("MASTER_TRANSITION_KIND_INVALID")
        patch = response.get("state_patch") or {}
        if not isinstance(patch, dict):
            raise ValueError("MASTER_STATE_PATCH_INVALID")
        overlap = RESERVED_PATCH_FIELDS.intersection(patch)
        if overlap:
            raise ValueError("MASTER_STATE_PATCH_RESERVED_FIELD")
        normalized = copy.deepcopy(response)
        normalized["kind"] = kind
        normalized["state_patch"] = copy.deepcopy(patch)
        if kind == "TERMINAL":
            terminal = str(response.get("terminal_status") or "").upper()
            if terminal not in TERMINAL_STATUS:
                raise ValueError("MASTER_TERMINAL_STATUS_INVALID")
            normalized["terminal_status"] = terminal
        return kind, normalized

    def _normalize_workers(self, kind: str, allocated_workers) -> list[dict]:
        if kind != "DISPATCH":
            if allocated_workers not in (None, []):
                raise ValueError("MASTER_WORKERS_ONLY_FOR_DISPATCH")
            return []
        workers = [] if allocated_workers is None else copy.deepcopy(allocated_workers)
        if not isinstance(workers, list):
            raise ValueError("MASTER_ALLOCATED_WORKERS_INVALID")
        if len(workers) > MAX_DYNAMIC_WORKERS:
            raise ValueError("MASTER_ALLOCATED_WORKERS_LIMIT")
        seen = set()
        for worker in workers:
            if not isinstance(worker, dict):
                raise ValueError("MASTER_ALLOCATED_WORKER_INVALID")
            worker_id = _text(worker.get("worker_id"), "MASTER_ALLOCATED_WORKER_ID_MISSING")
            session_id = _text(worker.get("session_id"), "MASTER_ALLOCATED_WORKER_SESSION_MISSING")
            objective = _text(worker.get("objective_sha256"), "MASTER_ALLOCATED_WORKER_OBJECTIVE_MISSING")
            if not worker_id.startswith("worker-") or worker_id == "worker-":
                raise ValueError("MASTER_ALLOCATED_WORKER_ID_INVALID")
            if worker_id in seen:
                raise ValueError("MASTER_ALLOCATED_WORKER_DUPLICATE")
            seen.add(worker_id)
            if not session_id or not objective:
                raise ValueError("MASTER_ALLOCATED_WORKER_INVALID")
        return workers

    def _transition_id(self, turn: dict, response: dict, workers: list[dict]) -> str:
        binding = {
            "protocol_version": turn.get("protocol_version"),
            "turn_id": turn.get("turn_id"),
            "project_id": turn.get("project_id"),
            "state_version": turn.get("state_version"),
            "actor_kind": turn.get("actor_kind"),
            "actor_id": turn.get("actor_id"),
            "session_id": turn.get("session_id"),
            "root_objective_sha256": turn.get("root_objective_sha256"),
            "acceptance_sha256": turn.get("acceptance_sha256"),
            "response": response,
            "allocated_workers": workers,
        }
        return "master-tx-" + _sha(binding)

    def _bound_worker_event(self, turn: dict, state: dict, transition_id: str):
        payload = turn.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("MASTER_TURN_PAYLOAD_INVALID")
        event_id = str(payload.get("worker_event_id") or "").strip()
        if not event_id:
            return None
        if self.worker_event_queue is None:
            raise ValueError("MASTER_WORKER_EVENT_QUEUE_REQUIRED")
        event = self.worker_event_queue.get(event_id)
        if event.get("project_id") != state.get("project_id"):
            raise ValueError("MASTER_WORKER_EVENT_PROJECT_MISMATCH")
        if event.get("root_objective_sha256") != state.get("goal_contract_sha256"):
            raise ValueError("MASTER_WORKER_EVENT_GOAL_MISMATCH")
        if event.get("acceptance_sha256") != state.get("acceptance_sha256"):
            raise ValueError("MASTER_WORKER_EVENT_ACCEPTANCE_MISMATCH")
        payload_worker = str(payload.get("worker_id") or "").strip()
        if payload_worker and payload_worker != event.get("worker_id"):
            raise ValueError("MASTER_WORKER_EVENT_WORKER_MISMATCH")
        if event.get("state") == "ACKED":
            if (
                str(payload.get("event") or "").upper() == "CONTINUE_CORE_AFTER_WORKER_EVENT"
                and event.get("ack_transition_id") == state.get("last_master_transition_id")
            ):
                return None
            if event.get("ack_transition_id") != transition_id:
                raise ValueError("WORKER_EVENT_ACK_CONFLICT")
        elif event.get("state") != "PENDING":
            raise ValueError("MASTER_WORKER_EVENT_STATE_INVALID")
        return event

    def _ack_worker_event(self, event, transition_id: str):
        if event is None:
            return None
        return self.worker_event_queue.acknowledge(event["event_id"], transition_id)

    def _next_active_workers(self, state: dict, kind: str, workers: list[dict], worker_event) -> list[dict] | None:
        if worker_event is None and kind != "DISPATCH":
            return None
        current = copy.deepcopy(state.get("active_workers") or [])
        if not isinstance(current, list):
            raise ValueError("MASTER_ACTIVE_WORKERS_INVALID")

        seen = set()
        validated = []
        for row in current:
            if not isinstance(row, dict):
                raise ValueError("MASTER_ACTIVE_WORKER_INVALID")
            worker_id = _text(row.get("worker_id"), "MASTER_ACTIVE_WORKER_ID_MISSING")
            if worker_id in seen:
                raise ValueError("MASTER_ACTIVE_WORKER_DUPLICATE")
            seen.add(worker_id)
            validated.append(row)
        current = validated

        if worker_event is not None:
            finished_worker_id = _text(worker_event.get("worker_id"), "MASTER_WORKER_EVENT_WORKER_MISSING")
            current = [row for row in current if row.get("worker_id") != finished_worker_id]
            seen.discard(finished_worker_id)

        if kind == "DISPATCH":
            for row in workers:
                worker_id = row["worker_id"]
                if worker_id in seen:
                    raise ValueError("MASTER_ACTIVE_WORKER_DUPLICATE")
                current.append(copy.deepcopy(row))
                seen.add(worker_id)

        if len(current) > MAX_DYNAMIC_WORKERS:
            raise ValueError("MASTER_ACTIVE_WORKERS_LIMIT")
        return current

    def _require_live_owner(self, turn: dict, now: dt.datetime) -> dict:
        project_id = turn["project_id"]
        session_id = turn["session_id"]
        lease = self.master_lease_store.load(project_id)
        if not isinstance(lease, dict):
            raise ValueError("MASTER_WINDOW_NOT_FOUND")
        if lease.get("session_id") != session_id:
            raise ValueError("MASTER_WINDOW_OWNER_MISMATCH")
        if lease.get("status") != "ACTIVE":
            raise ValueError("MASTER_WINDOW_NOT_ACTIVE")
        if not self.master_lease_store.is_active(project_id, now=now):
            raise ValueError("MASTER_WINDOW_LEASE_EXPIRED")
        return lease

    def _complete_operational_side_effect(self, kind: str, turn: dict, now: dt.datetime, *, replay: bool) -> dict | None:
        project_id = turn["project_id"]
        session_id = turn["session_id"]
        lease = self.master_lease_store.load(project_id)
        if not isinstance(lease, dict):
            raise ValueError("MASTER_WINDOW_NOT_FOUND")
        if lease.get("session_id") != session_id:
            raise ValueError("MASTER_WINDOW_OWNER_MISMATCH")

        if kind in {"CONTINUE", "DISPATCH", "WAIT"}:
            if lease.get("status") != "ACTIVE":
                if replay:
                    return lease
                raise ValueError("MASTER_WINDOW_NOT_ACTIVE")
            if not self.master_lease_store.is_active(project_id, now=now):
                if replay:
                    return lease
                raise ValueError("MASTER_WINDOW_LEASE_EXPIRED")
            return self.master_lease_store.heartbeat(project_id, session_id, now=now)

        if kind == "DRAIN":
            if lease.get("status") == "DRAINED":
                return lease
            if lease.get("status") != "ACTIVE":
                raise ValueError("MASTER_WINDOW_REPLAY_CONFLICT" if replay else "MASTER_WINDOW_NOT_ACTIVE")
            return self.master_lease_store.drain(project_id, session_id, reason="WINDOW_DRAIN", now=now)

        if kind == "TERMINAL":
            if lease.get("status") == "RELEASED":
                return lease
            if lease.get("status") != "ACTIVE":
                raise ValueError("MASTER_WINDOW_REPLAY_CONFLICT" if replay else "MASTER_WINDOW_NOT_ACTIVE")
            return self.master_lease_store.release(project_id, session_id, reason="PROJECT_TERMINAL", now=now)

        raise ValueError("MASTER_TRANSITION_KIND_INVALID")

    def apply(self, turn: dict, response: dict, *, allocated_workers=None, now=None) -> dict:
        now = _aware_utc(now)
        state = self.project_state_store.load()
        self._validate_turn_contract(turn, state)
        kind, normalized_response = self._normalize_response(response)
        workers = self._normalize_workers(kind, allocated_workers)
        transition_id = self._transition_id(turn, normalized_response, workers)
        turn_version = int(turn["state_version"])
        worker_event = self._bound_worker_event(turn, state, transition_id)
        if kind == "WAIT" and worker_event is None:
            raise ValueError("MASTER_WAIT_TRANSITION_REQUIRES_WORKER_EVENT")

        replayed = (
            state.get("last_master_transition_id") == transition_id
            and state.get("state_version") == turn_version + 1
        )
        if replayed:
            lease = self._complete_operational_side_effect(kind, turn, now, replay=True)
            acked_event = self._ack_worker_event(worker_event, transition_id)
            return {
                "transition_id": transition_id,
                "replayed": True,
                "state": self.project_state_store.load(),
                "lease": lease,
                "worker_event": acked_event,
            }

        if state.get("state_version") != turn_version:
            raise ValueError("MASTER_STATE_VERSION_CONFLICT")
        if state.get("status") != "ACTIVE":
            raise ValueError("PROJECT_STATE_TERMINAL")

        self._require_live_owner(turn, now)
        next_active_workers = self._next_active_workers(state, kind, workers, worker_event)

        changes = copy.deepcopy(normalized_response["state_patch"])
        if next_active_workers is not None:
            changes["active_workers"] = next_active_workers
        if kind == "TERMINAL":
            changes["status"] = normalized_response["terminal_status"]
        changes["last_master_transition_id"] = transition_id

        updated = self.project_state_store.update(turn_version, changes)

        # PROJECT_STATE is durable first. If either operational side effect fails,
        # exact replay repairs it without incrementing state_version again.
        lease = self._complete_operational_side_effect(kind, turn, now, replay=False)
        acked_event = self._ack_worker_event(worker_event, transition_id)
        return {
            "transition_id": transition_id,
            "replayed": False,
            "state": updated,
            "lease": lease,
            "worker_event": acked_event,
        }
