from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


PROTOCOL_VERSION = "scorp.chat-resource/v3"
UTC = dt.timezone.utc


def _utc_text(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("CHAT_RESOURCE_NOW_MUST_BE_AWARE")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("CHAT_RESOURCE_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise ValueError("CHAT_RESOURCE_TIMESTAMP_INVALID")
    return parsed.astimezone(UTC)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


class ChatResourceManagerV3:
    def __init__(self, path, *, creation_cooldown_seconds=60, throttle_backoff_seconds=300):
        self.path = Path(path)
        self.creation_cooldown_seconds = int(creation_cooldown_seconds)
        self.throttle_backoff_seconds = int(throttle_backoff_seconds)
        if self.creation_cooldown_seconds < 0:
            raise ValueError("CHAT_RESOURCE_CREATION_COOLDOWN_INVALID")
        if self.throttle_backoff_seconds <= 0:
            raise ValueError("CHAT_RESOURCE_THROTTLE_BACKOFF_INVALID")

    @staticmethod
    def _now(value=None) -> dt.datetime:
        value = value or dt.datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("CHAT_RESOURCE_NOW_MUST_BE_AWARE")
        return value.astimezone(UTC)

    def _load(self) -> dict:
        if not self.path.is_file():
            return {
                "protocol_version": PROTOCOL_VERSION,
                "creation_not_before": None,
                "throttle_not_before": None,
                "last_reservation_owner": None,
                "last_throttle_reason": None,
                "updated_at": None,
            }
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise ValueError("CHAT_RESOURCE_STATE_INVALID") from exc
        if not isinstance(value, dict):
            raise ValueError("CHAT_RESOURCE_STATE_INVALID")
        if value.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("CHAT_RESOURCE_PROTOCOL_INVALID")
        _parse_utc(value.get("creation_not_before"))
        _parse_utc(value.get("throttle_not_before"))
        return value

    def permission(self, *, now=None, needs_new_chat=True) -> dict:
        current = self._now(now)
        state = self._load()
        throttle_until = _parse_utc(state.get("throttle_not_before"))
        if throttle_until is not None and current < throttle_until:
            return {
                "allowed": False,
                "reason": "THROTTLE_CIRCUIT_OPEN",
                "not_before": _utc_text(throttle_until),
            }
        if not bool(needs_new_chat):
            return {"allowed": True, "reason": "REUSE_ALLOWED", "not_before": None}
        creation_until = _parse_utc(state.get("creation_not_before"))
        if creation_until is not None and current < creation_until:
            return {
                "allowed": False,
                "reason": "CREATION_COOLDOWN",
                "not_before": _utc_text(creation_until),
            }
        return {"allowed": True, "reason": "ALLOWED", "not_before": None}

    def reserve_new_chat(self, owner, *, now=None) -> dict:
        current = self._now(now)
        owner = _text(owner, "CHAT_RESOURCE_OWNER_EMPTY")
        permission = self.permission(now=current, needs_new_chat=True)
        if not permission["allowed"]:
            raise ValueError("CHAT_RESOURCE_NEW_CHAT_BLOCKED")
        state = self._load()
        not_before = current + dt.timedelta(seconds=self.creation_cooldown_seconds)
        state.update({
            "protocol_version": PROTOCOL_VERSION,
            "creation_not_before": _utc_text(not_before),
            "last_reservation_owner": owner,
            "updated_at": _utc_text(current),
        })
        _atomic_json(self.path, state)
        return dict(state)

    def note_throttle(self, reason, *, now=None) -> dict:
        current = self._now(now)
        reason = _text(reason, "CHAT_RESOURCE_THROTTLE_REASON_EMPTY")
        state = self._load()
        requested = current + dt.timedelta(seconds=self.throttle_backoff_seconds)
        existing = _parse_utc(state.get("throttle_not_before"))
        throttle_until = max(requested, existing) if existing is not None else requested
        state.update({
            "protocol_version": PROTOCOL_VERSION,
            "throttle_not_before": _utc_text(throttle_until),
            "last_throttle_reason": reason,
            "updated_at": _utc_text(current),
        })
        _atomic_json(self.path, state)
        return dict(state)
