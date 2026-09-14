from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

UTC = dt.timezone.utc
ACTIVE = "ACTIVE"


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("MASTER_LEASE_STORE_INVALID")
    return value


def _iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("MASTER_LEASE_TIME_NAIVE")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError("MASTER_LEASE_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise ValueError("MASTER_LEASE_TIME_INVALID")
    return parsed.astimezone(UTC)


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


class MasterWindowLeaseStore:
    def __init__(self, path):
        self.path = Path(path)

    def _all(self):
        return _load(self.path)

    def load(self, project_id: str):
        project = _text(project_id, "PROJECT_ID_EMPTY")
        entry = self._all().get(project)
        return dict(entry) if isinstance(entry, dict) else None

    def is_active(self, project_id: str, now=None) -> bool:
        now = now or dt.datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("MASTER_LEASE_TIME_NAIVE")
        entry = self.load(project_id)
        if not entry or entry.get("status") != ACTIVE:
            return False
        return _parse(entry.get("lease_until")) > now.astimezone(UTC)

    def acquire(self, project_id, session_id, turn_id, project_state_version_at_start, *, now=None, ttl_seconds=1500):
        project = _text(project_id, "PROJECT_ID_EMPTY")
        session = _text(session_id, "SESSION_ID_EMPTY")
        turn = _text(turn_id, "TURN_ID_EMPTY")
        now = (now or dt.datetime.now(UTC)).astimezone(UTC)
        ttl = int(ttl_seconds)
        if ttl <= 0:
            raise ValueError("MASTER_LEASE_TTL_INVALID")
        version = int(project_state_version_at_start)
        if version < 0:
            raise ValueError("PROJECT_STATE_VERSION_INVALID")

        value = self._all()
        current = value.get(project)
        if isinstance(current, dict) and current.get("status") == ACTIVE:
            live = _parse(current.get("lease_until")) > now
            if live:
                if current.get("session_id") == session:
                    return dict(current)
                raise ValueError("MASTER_WINDOW_ALREADY_ACTIVE")

        lease = {
            "protocol_version": "scorp.master-window-lease/v1",
            "project_id": project,
            "session_id": session,
            "window_id": session,
            "turn_id": turn,
            "project_state_version_at_start": version,
            "status": ACTIVE,
            "acquired_at": _iso(now),
            "heartbeat_at": _iso(now),
            "lease_until": _iso(now + dt.timedelta(seconds=ttl)),
        }
        value[project] = lease
        _atomic(self.path, value)
        return dict(lease)

    def heartbeat(self, project_id, session_id, *, now=None, ttl_seconds=1500):
        project = _text(project_id, "PROJECT_ID_EMPTY")
        session = _text(session_id, "SESSION_ID_EMPTY")
        now = (now or dt.datetime.now(UTC)).astimezone(UTC)
        ttl = int(ttl_seconds)
        if ttl <= 0:
            raise ValueError("MASTER_LEASE_TTL_INVALID")
        value = self._all()
        current = value.get(project)
        if not isinstance(current, dict) or current.get("status") != ACTIVE:
            raise ValueError("MASTER_WINDOW_NOT_ACTIVE")
        if current.get("session_id") != session:
            raise ValueError("MASTER_WINDOW_OWNER_MISMATCH")
        if _parse(current.get("lease_until")) <= now:
            raise ValueError("MASTER_WINDOW_LEASE_EXPIRED")
        current = dict(current)
        current["heartbeat_at"] = _iso(now)
        current["lease_until"] = _iso(now + dt.timedelta(seconds=ttl))
        value[project] = current
        _atomic(self.path, value)
        return dict(current)

    def _finish(self, project_id, session_id, status, reason, now):
        project = _text(project_id, "PROJECT_ID_EMPTY")
        session = _text(session_id, "SESSION_ID_EMPTY")
        reason = _text(reason, "MASTER_WINDOW_REASON_EMPTY")
        now = (now or dt.datetime.now(UTC)).astimezone(UTC)
        value = self._all()
        current = value.get(project)
        if not isinstance(current, dict):
            raise ValueError("MASTER_WINDOW_NOT_FOUND")
        if current.get("session_id") != session:
            raise ValueError("MASTER_WINDOW_OWNER_MISMATCH")
        current = dict(current)
        current["status"] = status
        current["lease_until"] = _iso(now)
        current["finished_at"] = _iso(now)
        if status == "DRAINED":
            current["drain_reason"] = reason
        else:
            current["release_reason"] = reason
        value[project] = current
        _atomic(self.path, value)
        return dict(current)

    def drain(self, project_id, session_id, *, reason="WINDOW_DRAIN", now=None):
        return self._finish(project_id, session_id, "DRAINED", reason, now)

    def release(self, project_id, session_id, *, reason, now=None):
        return self._finish(project_id, session_id, "RELEASED", reason, now)
