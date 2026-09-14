from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

from actor_response_journal_v3 import ActorResponseJournalV3
from gui_transport import render_actor_prompt_v3
from turn_scheduler_v3 import TurnSchedulerV3
from worker_event_queue_v3 import WorkerEventQueueV3


PROTOCOL = "scorp.master-worker/turn-v3"
MAX_WORKERS_PER_DISPATCH = 8
GUI_TIMEOUT_SECONDS = 1800


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def _require_text(value, error):
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


def _validate_turn(turn: dict) -> dict:
    if not isinstance(turn, dict) or turn.get("protocol_version") != PROTOCOL:
        raise ValueError("ACTOR_TURN_PROTOCOL_INVALID")
    for key in ("turn_id", "project_id", "actor_kind", "actor_id", "session_id", "root_objective_sha256", "acceptance_sha256"):
        _require_text(turn.get(key), "ACTOR_TURN_FIELD_MISSING_" + key)
    try:
        version = int(turn.get("state_version"))
    except Exception as exc:
        raise ValueError("ACTOR_STATE_VERSION_INVALID") from exc
    if version < 0:
        raise ValueError("ACTOR_STATE_VERSION_INVALID")
    kind = str(turn["actor_kind"]).upper()
    actor = str(turn["actor_id"])
    if kind == "MASTER":
        if actor != "A":
            raise ValueError("MASTER_IDENTITY_INVALID")
    elif kind == "WORKER":
        if actor == "A" or not actor.startswith("worker-"):
            raise ValueError("WORKER_ID_INVALID")
        _require_text(turn.get("objective_sha256"), "WORKER_OBJECTIVE_HASH_MISSING")
    else:
        raise ValueError("ACTOR_KIND_INVALID")
    return turn


def _child_id(prefix: str, parent: dict, discriminator: str) -> str:
    raw = "|".join((
        parent["project_id"], parent["turn_id"], str(parent["state_version"]),
        prefix, discriminator, parent["root_objective_sha256"], parent["acceptance_sha256"],
    ))
    return f"v3-{prefix.lower()}-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _worker_identity(parent: dict, objective: str, index: int):
    raw = "|".join((parent["project_id"], parent["turn_id"], objective, str(index)))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    worker_id = "worker-" + digest[:12]
    session_id = f"{worker_id}-session-{digest[12:22]}"
    return worker_id, session_id


class MasterWorkerRelayV3:
    def __init__(self, root, sessions, gui_turn, master_transition=None, turn_scheduler=None):
        self.root = Path(root)
        self.outbox = self.root / "master-worker-v3-outbox"
        self.inbox = self.root / "master-worker-v3-inbox"
        self.ledger_path = self.root / "master-worker-v3-ledger.json"
        self.sessions = sessions
        self.gui_turn = gui_turn
        self.master_transition = master_transition
        if turn_scheduler is None and master_transition is not None:
            turn_scheduler = TurnSchedulerV3(
                master_transition.project_state_store,
                master_transition.master_lease_store,
            )
        self.turn_scheduler = turn_scheduler
        self.worker_events = WorkerEventQueueV3(self.root / "worker-events-v3")
        self.response_journal = ActorResponseJournalV3(self.root / "actor-response-journal-v3.json")
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)

    def _ledger(self):
        value = _load(self.ledger_path, {}) or {}
        if not isinstance(value, dict):
            raise ValueError("ACTOR_LEDGER_INVALID")
        return value

    def _save_ledger(self, value):
        _atomic(self.ledger_path, value)

    def _pending(self, ledger, *, now=None):
        found = []
        for path in sorted(self.outbox.glob("*.json")):
            turn = _load(path)
            _validate_turn(turn)
            turn_id = turn["turn_id"]
            digest = _sha(turn)
            old = ledger.get(turn_id) or {}
            if old.get("envelope_sha256") and old["envelope_sha256"] != digest:
                raise ValueError("ACTOR_TURN_CONFLICT")
            if old.get("state") in {"ROUTED", "SUPERSEDED"}:
                continue
            journaled = self.response_journal.load(turn_id, digest)
            found.append({"turn_id": turn_id, "digest": digest, "turn": turn, "replayable": journaled is not None})
        if not found:
            return None

        if self.turn_scheduler is None:
            found.sort(key=lambda row: row["turn_id"])
            selected = found[0]
            return selected["digest"], selected["turn"]

        scheduled = self.turn_scheduler.select(found, ledger, now=now)
        superseded = scheduled.get("superseded") or []
        if superseded:
            now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            by_id = {row["turn_id"]: row for row in found}
            for row in superseded:
                turn_id = row["turn_id"]
                candidate = by_id.get(turn_id)
                if candidate is None:
                    raise ValueError("TURN_SCHEDULER_SUPERSEDE_UNKNOWN")
                turn = candidate["turn"]
                ledger[turn_id] = {
                    **(ledger.get(turn_id) or {}),
                    "state": "SUPERSEDED",
                    "envelope_sha256": candidate["digest"],
                    "actor_kind": turn["actor_kind"].upper(),
                    "actor_id": turn["actor_id"],
                    "session_id": turn["session_id"],
                    "superseded_reason": row["reason"],
                    "superseded_at": now,
                }
            self._save_ledger(ledger)

        selected = scheduled.get("selected")
        if selected is None:
            return None
        return selected["digest"], selected["turn"]

    def _master_state_context(self, expected_version):
        if self.master_transition is None:
            return {}
        state = self.master_transition.project_state_store.load()
        if int(state.get("state_version")) != int(expected_version):
            raise ValueError("MASTER_CHILD_STATE_CONTEXT_VERSION_CONFLICT")
        return {
            "project_state_version": state["state_version"],
            "project_state_sha256": _sha(state),
            "project_state": state,
        }

    def _write_child(self, child):
        _validate_turn(child)
        path = self.outbox / f"{child['turn_id']}.json"
        if path.exists():
            old = _load(path)
            if _sha(old) != _sha(child):
                raise ValueError("ACTOR_CHILD_CONFLICT")
            return
        _atomic(path, child)

    def _validate_dispatch(self, response):
        assignments = response.get("assignments")
        if not isinstance(assignments, list) or not assignments:
            raise ValueError("DISPATCH_ASSIGNMENTS_REQUIRED")
        if len(assignments) > MAX_WORKERS_PER_DISPATCH:
            raise ValueError("DISPATCH_WORKER_LIMIT")
        objectives = []
        for assignment in assignments:
            if not isinstance(assignment, dict):
                raise ValueError("DISPATCH_ASSIGNMENT_INVALID")
            objective = _require_text(assignment.get("objective_sha256"), "DISPATCH_OBJECTIVE_HASH_MISSING")
            objectives.append(objective)
        if len(set(objectives)) != len(objectives):
            raise ValueError("DISPATCH_OBJECTIVE_DUPLICATE")
        return assignments

    def _planned_workers(self, turn, response):
        assignments = self._validate_dispatch(response)
        workers = []
        for index, assignment in enumerate(assignments):
            objective = str(assignment["objective_sha256"])
            worker_id, worker_session = _worker_identity(turn, objective, index)
            workers.append({
                "worker_id": worker_id,
                "session_id": worker_session,
                "objective_sha256": objective,
                "assignment": copy.deepcopy(assignment),
            })
        return workers

    def _route(self, turn, response, *, committed_state_version=None):
        actor_kind = turn["actor_kind"].upper()
        response_kind = str(response.get("kind") or "").upper()
        child_state_version = turn["state_version"] if committed_state_version is None else committed_state_version
        children = []

        if actor_kind == "MASTER":
            if response_kind == "DISPATCH":
                assignments = self._validate_dispatch(response)
                for index, assignment in enumerate(assignments):
                    objective = str(assignment["objective_sha256"])
                    worker_id, worker_session = _worker_identity(turn, objective, index)
                    child = {
                        "protocol_version": PROTOCOL,
                        "turn_id": _child_id("worker", turn, worker_id),
                        "project_id": turn["project_id"],
                        "state_version": child_state_version,
                        "actor_kind": "WORKER",
                        "actor_id": worker_id,
                        "session_id": worker_session,
                        "root_objective_sha256": turn["root_objective_sha256"],
                        "acceptance_sha256": turn["acceptance_sha256"],
                        "objective_sha256": objective,
                        "payload": {
                            "event": "ASSIGNMENT",
                            "parent_turn_id": turn["turn_id"],
                            "controller_session_id": turn["session_id"],
                            "assignment": assignment,
                        },
                    }
                    self._write_child(child)
                    children.append(child["turn_id"])
                master_child = {
                    "protocol_version": PROTOCOL,
                    "turn_id": _child_id("master-core", turn, _sha(assignments)),
                    "project_id": turn["project_id"],
                    "state_version": child_state_version,
                    "actor_kind": "MASTER",
                    "actor_id": "A",
                    "session_id": turn["session_id"],
                    "root_objective_sha256": turn["root_objective_sha256"],
                    "acceptance_sha256": turn["acceptance_sha256"],
                    "payload": {
                        "event": "CONTINUE_CORE_AFTER_DISPATCH",
                        "parent_turn_id": turn["turn_id"],
                        "worker_turn_ids": list(children),
                        **self._master_state_context(child_state_version),
                    },
                }
                self._write_child(master_child)
                children.append(master_child["turn_id"])
            elif response_kind == "CONTINUE":
                child = {
                    "protocol_version": PROTOCOL,
                    "turn_id": _child_id("master-continue", turn, _sha(response)),
                    "project_id": turn["project_id"],
                    "state_version": child_state_version,
                    "actor_kind": "MASTER",
                    "actor_id": "A",
                    "session_id": turn["session_id"],
                    "root_objective_sha256": turn["root_objective_sha256"],
                    "acceptance_sha256": turn["acceptance_sha256"],
                    "payload": {
                        "event": "CONTINUE_CORE",
                        "parent_turn_id": turn["turn_id"],
                        "master_response": response,
                        **self._master_state_context(child_state_version),
                    },
                }
                self._write_child(child)
                children.append(child["turn_id"])
            elif response_kind == "WAIT":
                payload = turn.get("payload") or {}
                event_id = str(payload.get("worker_event_id") or "").strip() if isinstance(payload, dict) else ""
                if event_id and committed_state_version is not None:
                    context = self._master_state_context(child_state_version)
                    state = context.get("project_state") or {}
                    if not list(state.get("active_workers") or []):
                        child = {
                            "protocol_version": PROTOCOL,
                            "turn_id": _child_id("master-worker-event-followup", turn, event_id),
                            "project_id": turn["project_id"],
                            "state_version": child_state_version,
                            "actor_kind": "MASTER",
                            "actor_id": "A",
                            "session_id": turn["session_id"],
                            "root_objective_sha256": turn["root_objective_sha256"],
                            "acceptance_sha256": turn["acceptance_sha256"],
                            "payload": {
                                "event": "CONTINUE_CORE_AFTER_WORKER_EVENT",
                                "parent_turn_id": turn["turn_id"],
                                "worker_event_id": event_id,
                                "source_worker_turn_id": payload.get("source_worker_turn_id"),
                                "master_response": response,
                                **context,
                            },
                        }
                        self._write_child(child)
                        children.append(child["turn_id"])
            elif response_kind not in {"DRAIN", "TERMINAL"}:
                raise ValueError("MASTER_RESPONSE_KIND_INVALID")
        else:
            if response_kind not in {"HANDOFF", "BLOCKER"}:
                raise ValueError("WORKER_RESPONSE_KIND_INVALID")
            self.worker_events.enqueue(turn, response)
        return children

    def _commit_master_response(self, turn, response, *, now=None):
        if self.master_transition is None:
            return None
        response_kind = str(response.get("kind") or "").upper()
        if response_kind == "WAIT":
            payload = turn.get("payload") or {}
            if not isinstance(payload, dict):
                raise ValueError("MASTER_TURN_PAYLOAD_INVALID")
            if not str(payload.get("worker_event_id") or "").strip():
                return None
        allocated_workers = None
        if response_kind == "DISPATCH":
            allocated_workers = self._planned_workers(turn, response)
        return self.master_transition.apply(turn, response, allocated_workers=allocated_workers, now=now)

    async def run_once(self, *, now=None):
        ledger = self._ledger()
        selected = self._pending(ledger, now=now)
        if selected is None:
            return {"status": "IDLE"}
        digest, turn = selected
        turn_id = turn["turn_id"]
        project_id = turn["project_id"]
        session_id = turn["session_id"]
        actor_kind = turn["actor_kind"].upper()
        actor_id = turn["actor_id"]
        existing_url = self.sessions.resolve_actor_conversation(project_id, session_id, actor_kind, actor_id)

        journaled = self.response_journal.load(turn_id, digest)
        if journaled is None:
            started_at = dt.datetime.now(dt.timezone.utc)
            ledger = self._ledger()
            ledger[turn_id] = {
                **(ledger.get(turn_id) or {}),
                "state": "GUI_INFLIGHT",
                "envelope_sha256": digest,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "session_id": session_id,
                "started_at": started_at.isoformat().replace("+00:00", "Z"),
            }
            self._save_ledger(ledger)

            prompt = render_actor_prompt_v3(turn)
            response, snapshot, returned_url = await self.gui_turn(
                prompt,
                turn_id,
                actor_kind,
                conversation_url=existing_url,
                timeout_seconds=GUI_TIMEOUT_SECONDS,
            )
            if not isinstance(response, dict):
                raise ValueError("ACTOR_RESPONSE_NOT_OBJECT")
            if not returned_url:
                raise ValueError("ACTOR_CONVERSATION_URL_MISSING")
            canonical_url = self.sessions.record_session(
                project_id, session_id, actor_kind, actor_id, turn_id, returned_url
            )
            self.response_journal.record(turn_id, digest, response)
        else:
            response = journaled["response"]
            canonical_url = self.sessions.resolve_actor_conversation(project_id, session_id, actor_kind, actor_id)
            if not canonical_url:
                raise ValueError("ACTOR_RESPONSE_SESSION_MISSING")

        captured = {
            "protocol_version": "scorp.master-worker/response-v3",
            "turn": turn,
            "response": response,
            "conversation_url": canonical_url,
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        _atomic(self.inbox / f"{turn_id}.json", captured)

        transition_result = None
        committed_state_version = None
        if actor_kind == "MASTER":
            transition_result = self._commit_master_response(turn, response, now=now)
            if transition_result is not None:
                committed_state_version = transition_result["state"]["state_version"]

        if committed_state_version is None:
            children = self._route(turn, response)
        else:
            children = self._route(turn, response, committed_state_version=committed_state_version)
        ledger = self._ledger()
        ledger_row = {
            **(ledger.get(turn_id) or {}),
            "state": "ROUTED",
            "envelope_sha256": digest,
            "response_sha256": _sha(response),
            "conversation_url": canonical_url,
            "children": children,
            "routed_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if transition_result is not None:
            ledger_row["master_transition_id"] = transition_result["transition_id"]
            ledger_row["project_state_version"] = transition_result["state"]["state_version"]
            ledger_row["master_transition_replayed"] = bool(transition_result.get("replayed"))
        ledger[turn_id] = ledger_row
        self._save_ledger(ledger)
        return {
            "status": "ROUTED",
            "turn_id": turn_id,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "session_id": session_id,
            "conversation_url": canonical_url,
            "response": response,
            "children": children,
            "master_transition": transition_result,
        }
