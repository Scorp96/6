from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


VALID_ACTOR_KINDS = {"MASTER", "WORKER"}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("SESSION_REGISTRY_NOT_OBJECT")
    return value


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


def _utc_text() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class SessionRegistryV3:
    def __init__(self, path):
        self.path = Path(path)

    @staticmethod
    def _key(project_id: str, session_id: str) -> str:
        project = _text(project_id, "PROJECT_ID_EMPTY")
        session = _text(session_id, "SESSION_ID_EMPTY")
        return f"session:{project}:{session}"

    @staticmethod
    def _master_key(project_id: str) -> str:
        project = _text(project_id, "PROJECT_ID_EMPTY")
        return f"master-conversation:{project}"

    @staticmethod
    def _validate_actor(actor_kind: str, actor_id: str):
        kind = str(actor_kind or "").strip().upper()
        actor = _text(actor_id, "ACTOR_ID_EMPTY")
        if kind not in VALID_ACTOR_KINDS:
            raise ValueError("ACTOR_KIND_INVALID")
        if kind == "MASTER" and actor != "A":
            raise ValueError("MASTER_IDENTITY_INVALID")
        if kind == "WORKER" and (actor == "A" or not actor.startswith("worker-")):
            raise ValueError("WORKER_ID_INVALID")
        return kind, actor

    @staticmethod
    def _conversation_owners(value: dict, canonical: str, *, skip_key: str | None = None):
        owners = []
        for other_key, other in value.items():
            if other_key == skip_key or not str(other_key).startswith("session:"):
                continue
            if isinstance(other, dict) and other.get("conversation_url") == canonical:
                owners.append((other_key, other))
        return owners

    def get_session(self, project_id: str, session_id: str):
        entry = _load_json(self.path).get(self._key(project_id, session_id))
        if entry is None:
            return None
        if not isinstance(entry, dict):
            raise ValueError("SESSION_ENTRY_INVALID")
        return dict(entry)

    def get_session_url(self, project_id: str, session_id: str):
        entry = self.get_session(project_id, session_id)
        return entry.get("conversation_url") if entry else None

    def get_master_conversation(self, project_id: str):
        entry = _load_json(self.path).get(self._master_key(project_id))
        if entry is None:
            return None
        if not isinstance(entry, dict):
            raise ValueError("MASTER_CONVERSATION_ENTRY_INVALID")
        if entry.get("project_id") != _text(project_id, "PROJECT_ID_EMPTY"):
            raise ValueError("MASTER_CONVERSATION_PROJECT_MISMATCH")
        if entry.get("actor_kind") != "MASTER" or entry.get("actor_id") != "A":
            raise ValueError("MASTER_CONVERSATION_IDENTITY_INVALID")
        return dict(entry)

    def get_master_conversation_url(self, project_id: str):
        entry = self.get_master_conversation(project_id)
        return entry.get("conversation_url") if entry else None

    def resolve_actor_conversation(self, project_id: str, session_id: str, actor_kind: str, actor_id: str):
        kind, actor = self._validate_actor(actor_kind, actor_id)
        entry = self.get_session(project_id, session_id)
        if entry is not None:
            if entry.get("actor_kind") != kind or entry.get("actor_id") != actor:
                raise ValueError("SESSION_ACTOR_CHANGED")
            return entry.get("conversation_url")
        if kind == "MASTER" and actor == "A":
            return self.get_master_conversation_url(project_id)
        return None

    def rotate_master_conversation(
        self,
        project_id: str,
        *,
        predecessor_url: str,
        conversation_url: str,
        reason: str,
        turn_id: str,
    ) -> str:
        from gui_transport import validate_conversation_url

        project = _text(project_id, "PROJECT_ID_EMPTY")
        predecessor = validate_conversation_url(predecessor_url)
        canonical = validate_conversation_url(conversation_url)
        why = _text(reason, "MASTER_CONVERSATION_ROTATION_REASON_EMPTY")
        turn = _text(turn_id, "TURN_ID_EMPTY")
        if predecessor == "https://chatgpt.com/" or canonical == "https://chatgpt.com/":
            raise ValueError("MASTER_CONVERSATION_URL_MISSING")
        if predecessor == canonical:
            raise ValueError("MASTER_CONVERSATION_ROTATION_NOOP")

        value = _load_json(self.path)
        key = self._master_key(project)
        old = value.get(key)
        if not isinstance(old, dict):
            raise ValueError("MASTER_CONVERSATION_MISSING")
        if old.get("project_id") != project or old.get("actor_kind") != "MASTER" or old.get("actor_id") != "A":
            raise ValueError("MASTER_CONVERSATION_IDENTITY_INVALID")
        if old.get("conversation_url") != predecessor:
            raise ValueError("MASTER_CONVERSATION_PREDECESSOR_MISMATCH")
        if self._conversation_owners(value, canonical):
            raise ValueError("SESSION_CONVERSATION_OWNERSHIP_CONFLICT")

        value[key] = {
            **old,
            "conversation_url": canonical,
            "predecessor_conversation_url": predecessor,
            "rotation_reason": why,
            "rotation_turn_id": turn,
            "rotation_count": int(old.get("rotation_count") or 0) + 1,
            "updated_at": _utc_text(),
        }
        _atomic_json(self.path, value)
        return canonical

    def record_session(
        self,
        project_id: str,
        session_id: str,
        actor_kind: str,
        actor_id: str,
        turn_id: str,
        conversation_url: str,
    ) -> str:
        from gui_transport import validate_conversation_url

        project = _text(project_id, "PROJECT_ID_EMPTY")
        session = _text(session_id, "SESSION_ID_EMPTY")
        turn = _text(turn_id, "TURN_ID_EMPTY")
        kind, actor = self._validate_actor(actor_kind, actor_id)
        canonical = validate_conversation_url(conversation_url)
        if canonical == "https://chatgpt.com/":
            raise ValueError("SESSION_CONVERSATION_URL_MISSING")

        key = self._key(project, session)
        value = _load_json(self.path)
        old = value.get(key) or {}
        if old and not isinstance(old, dict):
            raise ValueError("SESSION_ENTRY_INVALID")

        if old:
            if old.get("actor_kind") != kind or old.get("actor_id") != actor:
                raise ValueError("SESSION_ACTOR_CHANGED")
            old_url = old.get("conversation_url")
            if old_url and old_url != canonical:
                raise ValueError("SESSION_CONVERSATION_CHANGED")

        owners = self._conversation_owners(value, canonical, skip_key=key)
        master_key = self._master_key(project)
        master_binding = value.get(master_key)

        if kind == "MASTER":
            if master_binding is not None:
                if not isinstance(master_binding, dict):
                    raise ValueError("MASTER_CONVERSATION_ENTRY_INVALID")
                if master_binding.get("actor_kind") != "MASTER" or master_binding.get("actor_id") != "A":
                    raise ValueError("MASTER_CONVERSATION_IDENTITY_INVALID")
                bound_url = master_binding.get("conversation_url")
                if bound_url and bound_url != canonical:
                    raise ValueError("MASTER_CONVERSATION_ROTATION_REQUIRED")
            for _, owner in owners:
                if (
                    owner.get("project_id") != project
                    or owner.get("actor_kind") != "MASTER"
                    or owner.get("actor_id") != "A"
                ):
                    raise ValueError("SESSION_CONVERSATION_OWNERSHIP_CONFLICT")
        elif owners:
            raise ValueError("SESSION_CONVERSATION_OWNERSHIP_CONFLICT")

        stamp = _utc_text()
        value[key] = {
            "project_id": project,
            "session_id": session,
            "actor_kind": kind,
            "actor_id": actor,
            "conversation_url": canonical,
            "created_turn_id": old.get("created_turn_id") or turn,
            "last_turn_id": turn,
            "updated_at": stamp,
        }

        if kind == "MASTER":
            if master_binding is None:
                value[master_key] = {
                    "project_id": project,
                    "actor_kind": "MASTER",
                    "actor_id": "A",
                    "conversation_url": canonical,
                    "created_session_id": session,
                    "last_session_id": session,
                    "created_turn_id": turn,
                    "last_turn_id": turn,
                    "rotation_count": 0,
                    "updated_at": stamp,
                }
            else:
                value[master_key] = {
                    **master_binding,
                    "last_session_id": session,
                    "last_turn_id": turn,
                    "updated_at": stamp,
                }

        _atomic_json(self.path, value)
        return canonical
