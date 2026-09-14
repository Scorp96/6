from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from project_state_v3 import ProjectStateStore, TERMINAL_STATUS


UTC = dt.timezone.utc
ARCHIVE_PROTOCOL = "scorp.project-archive/v1"
ROTATION_PROTOCOL = "scorp.project-rotation/v1"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat()


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
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError("PROJECT_ROTATION_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("PROJECT_ROTATION_INVALID")
    return value


def _slug(project_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", project_id).strip("._-")
    return value or "project"


class ProjectLifecycleV3:
    """Archive terminal V3 roots and leave deterministic rotation evidence."""

    def __init__(self, project_root, *, archive_root=None, rotation_path=None):
        self.root = Path(project_root)
        self.archive_root = Path(archive_root) if archive_root is not None else self.root.parent / "archive"
        self.rotation_path = Path(rotation_path) if rotation_path is not None else self.root.parent / "project-rotation.json"

    def _validate_terminal(self, state: dict) -> None:
        if state.get("status") not in TERMINAL_STATUS:
            raise ValueError("PROJECT_ARCHIVE_STATUS_NOT_TERMINAL")
        if state.get("active_master_window") is not None or state.get("active_workers"):
            raise ValueError("PROJECT_ARCHIVE_ACTIVE_ACTORS")

    def _new_destination(self, state: dict, now: dt.datetime) -> Path:
        stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        digest = hashlib.sha256(state["project_id"].encode("utf-8")).hexdigest()[:8]
        name = f"{_slug(state['project_id'])}-{digest}-v{state['state_version']}-{stamp}"
        return self.archive_root / name

    def _prepared_marker(self, state: dict, destination: Path, now: dt.datetime) -> dict:
        return {
            "protocol_version": ROTATION_PROTOCOL,
            "phase": "PREPARED",
            "previous_project_id": state["project_id"],
            "previous_goal_contract_sha256": state["goal_contract_sha256"],
            "previous_acceptance_sha256": state["acceptance_sha256"],
            "previous_state_version": state["state_version"],
            "archive_path": str(destination),
            "prepared_at": _iso(now),
        }

    def _marker_matches_state(self, marker: dict, state: dict) -> bool:
        return (
            marker.get("protocol_version") == ROTATION_PROTOCOL
            and marker.get("phase") == "PREPARED"
            and marker.get("previous_project_id") == state.get("project_id")
            and marker.get("previous_goal_contract_sha256") == state.get("goal_contract_sha256")
            and marker.get("previous_acceptance_sha256") == state.get("acceptance_sha256")
            and marker.get("previous_state_version") == state.get("state_version")
        )

    def _destination_from_marker(self, marker: dict) -> Path:
        raw = str(marker.get("archive_path") or "").strip()
        if not raw:
            raise ValueError("PROJECT_ROTATION_RECOVERY_INVALID")
        destination = Path(raw)
        try:
            if destination.parent.resolve() != self.archive_root.resolve():
                raise ValueError("PROJECT_ROTATION_RECOVERY_INVALID")
        except OSError as exc:
            raise ValueError("PROJECT_ROTATION_RECOVERY_INVALID") from exc
        return destination

    def _archive_manifest(self, state: dict, destination: Path, prepared_at: str) -> dict:
        return {
            "protocol_version": ARCHIVE_PROTOCOL,
            "project_id": state["project_id"],
            "master_identity": state["master_identity"],
            "status": state["status"],
            "state_version": state["state_version"],
            "goal_contract_sha256": state["goal_contract_sha256"],
            "acceptance_sha256": state["acceptance_sha256"],
            "archive_path": str(destination),
            "prepared_at": prepared_at,
        }

    def _finalize_marker(self, marker: dict, now: dt.datetime) -> dict:
        final = dict(marker)
        final["phase"] = "ARCHIVED"
        final["archived_at"] = _iso(now)
        _atomic_json(self.rotation_path, final)
        return final

    def _recover_prepared(self, marker: dict, now: dt.datetime) -> dict:
        if marker.get("protocol_version") != ROTATION_PROTOCOL or marker.get("phase") != "PREPARED":
            raise ValueError("PROJECT_ARCHIVE_STATE_MISSING")
        destination = self._destination_from_marker(marker)
        state_path = destination / "project-state.json"
        if not state_path.is_file():
            raise ValueError("PROJECT_ROTATION_RECOVERY_INVALID")
        state = ProjectStateStore(state_path).load()
        self._validate_terminal(state)
        if not self._marker_matches_state(marker, state):
            raise ValueError("PROJECT_ROTATION_RECOVERY_INVALID")
        manifest_path = destination / "archive-manifest.json"
        if not manifest_path.is_file():
            _atomic_json(manifest_path, self._archive_manifest(state, destination, marker["prepared_at"]))
        self.root.mkdir(parents=True, exist_ok=True)
        self._finalize_marker(marker, now)
        return {
            "status": state["status"],
            "project_id": state["project_id"],
            "archive_path": str(destination),
            "recovered": True,
        }

    def archive_terminal(self, *, now=None) -> dict:
        now = now or _utc_now()
        if now.tzinfo is None:
            raise ValueError("PROJECT_ARCHIVE_NOW_MUST_BE_AWARE")
        now = now.astimezone(UTC)
        state_path = self.root / "project-state.json"

        if not state_path.is_file():
            if not self.rotation_path.is_file():
                raise ValueError("PROJECT_ARCHIVE_STATE_MISSING")
            return self._recover_prepared(_read_json(self.rotation_path), now)

        state = ProjectStateStore(state_path).load()
        self._validate_terminal(state)
        marker = None
        if self.rotation_path.is_file():
            candidate = _read_json(self.rotation_path)
            if candidate.get("phase") == "PREPARED" and self._marker_matches_state(candidate, state):
                marker = candidate

        self.archive_root.mkdir(parents=True, exist_ok=True)
        if marker is None:
            destination = self._new_destination(state, now)
            marker = self._prepared_marker(state, destination, now)
            _atomic_json(self.rotation_path, marker)
        else:
            destination = self._destination_from_marker(marker)

        if destination.exists():
            raise ValueError("PROJECT_ARCHIVE_DESTINATION_EXISTS")

        _atomic_json(
            self.root / "archive-manifest.json",
            self._archive_manifest(state, destination, marker["prepared_at"]),
        )
        self.root.replace(destination)
        self.root.mkdir(parents=True, exist_ok=True)
        self._finalize_marker(marker, now)
        return {
            "status": state["status"],
            "project_id": state["project_id"],
            "archive_path": str(destination),
            "recovered": False,
        }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Manage terminal Scorp Master/Worker V3 project lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)
    archive = sub.add_parser("archive-terminal")
    archive.add_argument("--project-root", required=True)
    archive.add_argument("--archive-root")
    args = parser.parse_args()

    if args.command == "archive-terminal":
        result = ProjectLifecycleV3(args.project_root, archive_root=args.archive_root).archive_terminal()
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    raise ValueError("PROJECT_LIFECYCLE_COMMAND_INVALID")


if __name__ == "__main__":
    raise SystemExit(_main())
