from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from gui_transport import render_role_prompt


PROTOCOL = "scorp.gui-role-relay/turn-v1"
ROLE_TURN_BUDGET_SECONDS = 1500
ROLE_GUI_TIMEOUT_SECONDS = 1800
HANDOFF_RESERVE_SECONDS = 300


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: dict) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def _validate_turn(turn: dict) -> dict:
    if not isinstance(turn, dict) or turn.get("protocol_version") != PROTOCOL:
        raise ValueError("ROLE_TURN_PROTOCOL_INVALID")
    for key in ("turn_id", "mission_id", "from_role", "to_role", "root_objective_sha256", "acceptance_sha256"):
        if not str(turn.get(key) or "").strip():
            raise ValueError("ROLE_TURN_FIELD_MISSING_" + key)
    if str(turn["to_role"]).upper() not in {"A", "B", "C"}:
        raise ValueError("ROLE_INVALID")
    try:
        generation = int(turn.get("generation"))
    except Exception as exc:
        raise ValueError("ROLE_GENERATION_INVALID") from exc
    if generation < 0:
        raise ValueError("ROLE_GENERATION_INVALID")
    if str(turn["to_role"]).upper() in {"B", "C"} and not str(turn.get("objective_sha256") or "").strip():
        raise ValueError("WORKER_OBJECTIVE_HASH_MISSING")
    return turn


def _validate_response(turn: dict, response: dict) -> dict:
    if not isinstance(response, dict):
        raise ValueError("ROLE_RESPONSE_NOT_OBJECT")
    target = str(turn["to_role"]).upper()
    kind = str(response.get("kind") or "").upper()
    if target == "A":
        if kind not in {"DISPATCH", "WAIT", "TERMINAL"}:
            raise ValueError("CONTROLLER_RESPONSE_KIND_INVALID")
        if kind == "DISPATCH":
            assignments = response.get("assignments")
            if not isinstance(assignments, list) or not assignments:
                raise ValueError("DISPATCH_ASSIGNMENTS_REQUIRED")
            seen = set()
            for assignment in assignments:
                if not isinstance(assignment, dict):
                    raise ValueError("DISPATCH_ASSIGNMENT_INVALID")
                slot = str(assignment.get("worker_slot") or "").upper()
                if slot not in {"B", "C"} or slot in seen:
                    raise ValueError("DISPATCH_WORKER_SLOT_INVALID")
                seen.add(slot)
                if not str(assignment.get("objective_sha256") or "").strip():
                    raise ValueError("DISPATCH_OBJECTIVE_HASH_MISSING")
    else:
        if kind not in {"HANDOFF", "BLOCKER"}:
            raise ValueError("WORKER_RESPONSE_KIND_INVALID")
        slot = str(response.get("worker_slot") or target).upper()
        if slot != target:
            raise ValueError("WORKER_SLOT_MISMATCH")
    return response


def _child_turn_id(prefix: str, parent: dict, discriminator: str) -> str:
    raw = "|".join((
        str(parent["mission_id"]), str(parent["turn_id"]), str(parent["generation"]), prefix, discriminator,
        str(parent["root_objective_sha256"]), str(parent["acceptance_sha256"]),
    ))
    return f"relay-{prefix.lower()}-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class RoleRelayRuntime:
    def __init__(self, bridge_root, conversations, gui_turn):
        self.root = Path(bridge_root)
        self.outbox = self.root / "role-relay-outbox"
        self.inbox = self.root / "role-relay-inbox"
        self.ledger_path = self.root / "role-relay-ledger.json"
        self.conversations = conversations
        self.gui_turn = gui_turn
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.inbox.mkdir(parents=True, exist_ok=True)

    def _ledger(self):
        value = _load_json(self.ledger_path, {}) or {}
        return value if isinstance(value, dict) else {}

    def _persist_ledger(self, ledger):
        _atomic_json(self.ledger_path, ledger)

    def _pending_turn(self, ledger):
        by_id = {}
        for path in sorted(self.outbox.glob("*.json")):
            turn = _load_json(path, None)
            if not isinstance(turn, dict):
                continue
            _validate_turn(turn)
            turn_id = str(turn["turn_id"])
            digest = _sha(turn)
            if turn_id in by_id and by_id[turn_id][0] != digest:
                raise ValueError("ROLE_TURN_CONFLICT")
            by_id[turn_id] = (digest, turn)
        for turn_id in sorted(by_id):
            digest, turn = by_id[turn_id]
            entry = ledger.get(turn_id) or {}
            if entry.get("envelope_sha256") and entry["envelope_sha256"] != digest:
                raise ValueError("ROLE_TURN_CONFLICT")
            if entry.get("state") == "ROUTED":
                continue
            return digest, turn
        return None

    def _write_child(self, child: dict):
        _validate_turn(child)
        path = self.outbox / f"{child['turn_id']}.json"
        if path.exists():
            old = _load_json(path, None)
            if _sha(old) != _sha(child):
                raise ValueError("ROLE_CHILD_CONFLICT")
            return
        _atomic_json(path, child)

    def _route(self, turn: dict, response: dict):
        target = str(turn["to_role"]).upper()
        generation = int(turn["generation"])
        children = []
        if target == "A" and response["kind"].upper() == "DISPATCH":
            for assignment in response["assignments"]:
                slot = str(assignment["worker_slot"]).upper()
                objective_hash = str(assignment["objective_sha256"])
                child = {
                    "protocol_version": PROTOCOL,
                    "turn_id": _child_turn_id(slot, turn, objective_hash),
                    "mission_id": turn["mission_id"],
                    "generation": generation + 1,
                    "from_role": "A",
                    "to_role": slot,
                    "role_kind": "WORKER",
                    "root_objective_sha256": turn["root_objective_sha256"],
                    "acceptance_sha256": turn["acceptance_sha256"],
                    "objective_sha256": objective_hash,
                    "role_turn_budget_seconds": ROLE_TURN_BUDGET_SECONDS,
                    "handoff_reserve_seconds": HANDOFF_RESERVE_SECONDS,
                    "payload": {
                        "event": "ASSIGNMENT",
                        "parent_turn_id": turn["turn_id"],
                        "assignment": assignment,
                    },
                }
                self._write_child(child)
                children.append(child["turn_id"])
        elif target in {"B", "C"}:
            child = {
                "protocol_version": PROTOCOL,
                "turn_id": _child_turn_id("A", turn, _sha(response)),
                "mission_id": turn["mission_id"],
                "generation": generation,
                "from_role": target,
                "to_role": "A",
                "role_kind": "CONTROLLER",
                "root_objective_sha256": turn["root_objective_sha256"],
                "acceptance_sha256": turn["acceptance_sha256"],
                "role_turn_budget_seconds": ROLE_TURN_BUDGET_SECONDS,
                "handoff_reserve_seconds": HANDOFF_RESERVE_SECONDS,
                "payload": {
                    "event": "WORKER_" + response["kind"].upper(),
                    "parent_turn_id": turn["turn_id"],
                    "worker_slot": target,
                    "objective_sha256": turn.get("objective_sha256"),
                    "worker_response": response,
                },
            }
            self._write_child(child)
            children.append(child["turn_id"])
        return children

    async def run_once(self):
        ledger = self._ledger()
        selected = self._pending_turn(ledger)
        if selected is None:
            return {"status": "IDLE"}
        digest, turn = selected
        turn_id = str(turn["turn_id"])
        mission_id = str(turn["mission_id"])
        role = str(turn["to_role"]).upper()
        existing_url = self.conversations.get_role_url(mission_id, role)
        now = dt.datetime.now(dt.timezone.utc)
        ledger[turn_id] = {
            "state": "GUI_INFLIGHT",
            "envelope_sha256": digest,
            "role": role,
            "started_at": now.isoformat().replace("+00:00", "Z"),
            "lease_until": (now + dt.timedelta(seconds=ROLE_GUI_TIMEOUT_SECONDS)).isoformat().replace("+00:00", "Z"),
        }
        self._persist_ledger(ledger)

        prompt = render_role_prompt(turn)
        response, snapshot, returned_url = await self.gui_turn(
            prompt, turn_id, conversation_url=existing_url, timeout_seconds=ROLE_GUI_TIMEOUT_SECONDS)
        _validate_response(turn, response)
        if not returned_url:
            raise ValueError("ROLE_CONVERSATION_URL_MISSING")
        canonical_url = self.conversations.record_role(mission_id, role, turn_id, returned_url)

        captured = {
            "protocol_version": "scorp.gui-role-relay/response-v1",
            "turn": turn,
            "response": response,
            "conversation_url": canonical_url,
            "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        _atomic_json(self.inbox / f"{turn_id}.json", captured)
        ledger = self._ledger()
        ledger[turn_id] = {
            **(ledger.get(turn_id) or {}),
            "state": "RESPONSE_CAPTURED",
            "response_sha256": _sha(response),
            "conversation_url": canonical_url,
        }
        self._persist_ledger(ledger)

        children = self._route(turn, response)
        ledger = self._ledger()
        ledger[turn_id] = {
            **(ledger.get(turn_id) or {}),
            "state": "ROUTED",
            "children": children,
            "routed_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._persist_ledger(ledger)
        return {
            "status": "ROUTED",
            "turn_id": turn_id,
            "role": role,
            "response": response,
            "conversation_url": canonical_url,
            "children": children,
        }
