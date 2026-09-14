from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


class ActorResponseJournalV3:
    """Durable exactly-once journal for GPT actor responses."""

    def __init__(self, path):
        self.path = Path(path)

    def _load_all(self) -> dict:
        if not self.path.is_file():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("ACTOR_RESPONSE_JOURNAL_INVALID")
        return value

    def load(self, turn_id: str, turn_sha256: str):
        turn_id = _text(turn_id, "ACTOR_RESPONSE_TURN_ID_MISSING")
        turn_sha256 = _text(turn_sha256, "ACTOR_RESPONSE_TURN_SHA_MISSING")
        row = self._load_all().get(turn_id)
        if row is None:
            return None
        if not isinstance(row, dict):
            raise ValueError("ACTOR_RESPONSE_JOURNAL_INVALID")
        if row.get("turn_sha256") != turn_sha256:
            raise ValueError("ACTOR_RESPONSE_TURN_CONFLICT")
        response = row.get("response")
        if not isinstance(response, dict):
            raise ValueError("ACTOR_RESPONSE_JOURNAL_INVALID")
        if row.get("response_sha256") != _sha(response):
            raise ValueError("ACTOR_RESPONSE_JOURNAL_CORRUPT")
        return copy.deepcopy(row)

    def record(self, turn_id: str, turn_sha256: str, response: dict) -> dict:
        turn_id = _text(turn_id, "ACTOR_RESPONSE_TURN_ID_MISSING")
        turn_sha256 = _text(turn_sha256, "ACTOR_RESPONSE_TURN_SHA_MISSING")
        if not isinstance(response, dict):
            raise ValueError("ACTOR_RESPONSE_NOT_OBJECT")
        response = copy.deepcopy(response)
        response_sha256 = _sha(response)
        rows = self._load_all()
        old = rows.get(turn_id)
        if old is not None:
            if not isinstance(old, dict):
                raise ValueError("ACTOR_RESPONSE_JOURNAL_INVALID")
            if old.get("turn_sha256") != turn_sha256:
                raise ValueError("ACTOR_RESPONSE_TURN_CONFLICT")
            if old.get("response_sha256") != response_sha256 or old.get("response") != response:
                raise ValueError("ACTOR_RESPONSE_CONFLICT")
            return {
                "status": "ALREADY_RECORDED",
                "turn_id": turn_id,
                "response_sha256": response_sha256,
            }

        rows[turn_id] = {
            "turn_id": turn_id,
            "turn_sha256": turn_sha256,
            "response_sha256": response_sha256,
            "response": response,
        }
        _atomic(self.path, rows)
        return {
            "status": "RECORDED",
            "turn_id": turn_id,
            "response_sha256": response_sha256,
        }
