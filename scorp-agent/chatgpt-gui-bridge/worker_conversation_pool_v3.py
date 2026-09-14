from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


PROTOCOL_VERSION = "scorp.worker-conversation-pool/v3"
UTC = dt.timezone.utc


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


def _utc_text() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


class WorkerConversationPoolV3:
    """Durable bounded pool of reusable ChatGPT conversations for logical workers.

    Worker identities remain ephemeral. Slots own conversation URLs; leases bind
    one logical worker turn to one slot at a time. Releasing a lease preserves
    the conversation URL for the next worker assignment.
    """

    def __init__(self, path, *, pool_size=3):
        self.path = Path(path)
        self.pool_size = int(pool_size)
        if self.pool_size < 1 or self.pool_size > 8:
            raise ValueError("WORKER_POOL_SIZE_INVALID")

    def _fresh(self) -> dict:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "pool_size": self.pool_size,
            "slots": {
                f"worker-slot-{i}": {
                    "slot_id": f"worker-slot-{i}",
                    "conversation_url": None,
                    "lease": None,
                    "last_released_at": None,
                    "updated_at": None,
                }
                for i in range(1, self.pool_size + 1)
            },
        }

    def _load(self) -> dict:
        if not self.path.is_file():
            return self._fresh()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise ValueError("WORKER_POOL_STATE_INVALID") from exc
        if not isinstance(value, dict) or value.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("WORKER_POOL_STATE_INVALID")
        if int(value.get("pool_size") or 0) != self.pool_size:
            raise ValueError("WORKER_POOL_SIZE_MISMATCH")
        slots = value.get("slots")
        if not isinstance(slots, dict) or len(slots) != self.pool_size:
            raise ValueError("WORKER_POOL_SLOTS_INVALID")
        for i in range(1, self.pool_size + 1):
            slot_id = f"worker-slot-{i}"
            slot = slots.get(slot_id)
            if not isinstance(slot, dict) or slot.get("slot_id") != slot_id:
                raise ValueError("WORKER_POOL_SLOT_INVALID")
            lease = slot.get("lease")
            if lease is not None and not isinstance(lease, dict):
                raise ValueError("WORKER_POOL_LEASE_INVALID")
        return value

    @staticmethod
    def _identity(project_id, actor_id, session_id, turn_id) -> dict:
        project = _text(project_id, "PROJECT_ID_EMPTY")
        actor = _text(actor_id, "ACTOR_ID_EMPTY")
        session = _text(session_id, "SESSION_ID_EMPTY")
        turn = _text(turn_id, "TURN_ID_EMPTY")
        if actor == "A" or not actor.startswith("worker-"):
            raise ValueError("WORKER_ID_INVALID")
        return {
            "project_id": project,
            "actor_id": actor,
            "session_id": session,
            "turn_id": turn,
        }

    @staticmethod
    def _lease_matches(lease, identity) -> bool:
        return isinstance(lease, dict) and all(lease.get(k) == v for k, v in identity.items())

    @staticmethod
    def _public(slot: dict) -> dict:
        out = {
            "slot_id": slot["slot_id"],
            "conversation_url": slot.get("conversation_url"),
        }
        lease = slot.get("lease")
        if isinstance(lease, dict):
            out.update({
                "project_id": lease.get("project_id"),
                "actor_id": lease.get("actor_id"),
                "session_id": lease.get("session_id"),
                "turn_id": lease.get("turn_id"),
                "acquired_at": lease.get("acquired_at"),
            })
        return out

    def acquire(self, project_id, actor_id, session_id, turn_id):
        identity = self._identity(project_id, actor_id, session_id, turn_id)
        state = self._load()
        slots = state["slots"]

        for slot_id in sorted(slots):
            slot = slots[slot_id]
            if self._lease_matches(slot.get("lease"), identity):
                return self._public(slot)

        for slot in slots.values():
            lease = slot.get("lease")
            if isinstance(lease, dict) and (
                lease.get("project_id") == identity["project_id"]
                and lease.get("actor_id") == identity["actor_id"]
                and lease.get("session_id") == identity["session_id"]
            ):
                raise ValueError("WORKER_SLOT_LEASE_CONFLICT")

        for slot_id in sorted(slots):
            slot = slots[slot_id]
            if slot.get("lease") is not None:
                continue
            stamp = _utc_text()
            slot["lease"] = {**identity, "acquired_at": stamp}
            slot["updated_at"] = stamp
            _atomic_json(self.path, state)
            return self._public(slot)
        return None

    def resolve(self, project_id, actor_id, session_id, turn_id):
        identity = self._identity(project_id, actor_id, session_id, turn_id)
        state = self._load()
        for slot_id in sorted(state["slots"]):
            slot = state["slots"][slot_id]
            if self._lease_matches(slot.get("lease"), identity):
                return self._public(slot)
        return None

    def active_leases(self, project_id=None):
        project = None if project_id is None else _text(project_id, "PROJECT_ID_EMPTY")
        state = self._load()
        leases = []
        for slot_id in sorted(state["slots"]):
            slot = state["slots"][slot_id]
            lease = slot.get("lease")
            if not isinstance(lease, dict):
                continue
            if project is not None and lease.get("project_id") != project:
                continue
            leases.append(self._public(slot))
        return leases

    def _leased_slot(self, state, identity):
        for slot_id in sorted(state["slots"]):
            slot = state["slots"][slot_id]
            if self._lease_matches(slot.get("lease"), identity):
                return slot
        raise ValueError("WORKER_SLOT_LEASE_MISMATCH")

    def bind_conversation(self, project_id, actor_id, session_id, turn_id, conversation_url):
        from gui_transport import validate_conversation_url

        identity = self._identity(project_id, actor_id, session_id, turn_id)
        canonical = validate_conversation_url(conversation_url)
        if canonical == "https://chatgpt.com/":
            raise ValueError("WORKER_SLOT_CONVERSATION_URL_MISSING")
        state = self._load()
        slot = self._leased_slot(state, identity)
        old_url = slot.get("conversation_url")
        if old_url and old_url != canonical:
            raise ValueError("WORKER_SLOT_CONVERSATION_CHANGED")
        stamp = _utc_text()
        slot["conversation_url"] = canonical
        slot["updated_at"] = stamp
        _atomic_json(self.path, state)
        return self._public(slot)

    def release(self, project_id, actor_id, session_id, turn_id):
        identity = self._identity(project_id, actor_id, session_id, turn_id)
        state = self._load()
        slot = self._leased_slot(state, identity)
        stamp = _utc_text()
        slot["lease"] = None
        slot["last_released_at"] = stamp
        slot["updated_at"] = stamp
        _atomic_json(self.path, state)
        return self._public(slot)
