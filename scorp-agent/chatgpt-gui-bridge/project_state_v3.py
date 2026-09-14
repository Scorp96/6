from __future__ import annotations

import copy
import json
from pathlib import Path


PROTOCOL = "scorp.project-state/v1"
VALID_STATUS = {"ACTIVE", "COMPLETE", "HARD_BLOCKED"}
TERMINAL_STATUS = {"COMPLETE", "HARD_BLOCKED"}
IMMUTABLE_FIELDS = {
    "protocol_version",
    "project_id",
    "master_identity",
    "goal_contract_sha256",
    "acceptance_sha256",
}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("PROJECT_STATE_NOT_OBJECT")
    return value


def _require_text(state: dict, key: str) -> None:
    if not str(state.get(key) or "").strip():
        raise ValueError("PROJECT_STATE_FIELD_MISSING_" + key)


def _validate(state: dict) -> dict:
    if not isinstance(state, dict):
        raise ValueError("PROJECT_STATE_NOT_OBJECT")
    if state.get("protocol_version") != PROTOCOL:
        raise ValueError("PROJECT_STATE_PROTOCOL_INVALID")
    for key in (
        "project_id",
        "master_identity",
        "goal_contract_sha256",
        "acceptance_sha256",
        "status",
        "current_phase",
        "next_exact_action",
    ):
        _require_text(state, key)
    if state.get("master_identity") != "A":
        raise ValueError("MASTER_IDENTITY_INVALID")
    try:
        version = int(state.get("state_version"))
    except Exception as exc:
        raise ValueError("PROJECT_STATE_VERSION_INVALID") from exc
    if version < 0 or version != state.get("state_version"):
        raise ValueError("PROJECT_STATE_VERSION_INVALID")
    if state.get("status") not in VALID_STATUS:
        raise ValueError("PROJECT_STATE_STATUS_INVALID")
    for key in ("active_workers", "completed", "blocked", "evidence", "commits", "tests"):
        if not isinstance(state.get(key), list):
            raise ValueError("PROJECT_STATE_LIST_INVALID_" + key)
    if state.get("active_master_window") is not None and not isinstance(state.get("active_master_window"), dict):
        raise ValueError("PROJECT_MASTER_WINDOW_INVALID")
    return state


class ProjectStateStore:
    def __init__(self, path):
        self.path = Path(path)

    def create(self, initial: dict) -> dict:
        if self.path.exists():
            raise ValueError("PROJECT_STATE_ALREADY_EXISTS")
        state = copy.deepcopy(initial)
        _validate(state)
        if state["state_version"] != 0:
            raise ValueError("PROJECT_STATE_INITIAL_VERSION_INVALID")
        _atomic_json(self.path, state)
        return copy.deepcopy(state)

    def load(self) -> dict:
        if not self.path.is_file():
            raise ValueError("PROJECT_STATE_MISSING")
        state = _read_json(self.path)
        _validate(state)
        return copy.deepcopy(state)

    def update(self, expected_version: int, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise ValueError("PROJECT_STATE_CHANGES_INVALID")
        current = self.load()
        if current["state_version"] != expected_version:
            raise ValueError("PROJECT_STATE_VERSION_CONFLICT")
        if current["status"] in TERMINAL_STATUS:
            raise ValueError("PROJECT_STATE_TERMINAL")

        for field in IMMUTABLE_FIELDS:
            if field not in changes:
                continue
            if changes[field] == current[field]:
                continue
            if field == "goal_contract_sha256":
                raise ValueError("PROJECT_GOAL_IMMUTABLE")
            if field == "acceptance_sha256":
                raise ValueError("PROJECT_ACCEPTANCE_IMMUTABLE")
            raise ValueError("PROJECT_IDENTITY_IMMUTABLE")

        if "state_version" in changes and changes["state_version"] != expected_version + 1:
            raise ValueError("PROJECT_STATE_VERSION_MANAGED")

        updated = copy.deepcopy(current)
        updated.update(copy.deepcopy(changes))
        updated["state_version"] = expected_version + 1
        _validate(updated)
        _atomic_json(self.path, updated)
        return copy.deepcopy(updated)
