"""Fail-closed V4 candidate runtime probe.

This entrypoint deliberately performs no browser discovery and does not import
the legacy JSON relay.  It is a small operational seam for checking that the
SQLite candidate can be opened before a caller injects a real browser engine.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


BRIDGE_ROOT = pathlib.Path(__file__).resolve().parent
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError  # noqa: E402


WORKER_CAPACITY = 2
REASONING_MODEL = "GPT-5.6 Sol"


def describe(*, database_path: pathlib.Path, project_id: str, allowed_roots: list[pathlib.Path]) -> dict[str, Any]:
    store = StateStore(database_path, allowed_roots=allowed_roots)
    try:
        try:
            state = store.get_project_state(project_id)
        except StoreInvariantError as exc:
            if str(exc) != "PROJECT_NOT_FOUND":
                raise
            state = None
        payload: dict[str, Any] = {
            "runtime": "SCORP_V4_CANDIDATE",
            "reasoning_model": REASONING_MODEL,
            "project_id": project_id,
            "database_path": str(store.path),
            "queue_authority": "sqlite",
            "legacy_json_role": "import_or_read_only_compatibility",
            "legacy_runtime": "NOT_USED",
            "worker_capacity": WORKER_CAPACITY,
            "browser_io": "NOT_ATTEMPTED",
            "status": str(state["status"]) if state else "UNINITIALIZED",
            "state_version": int(state["state_version"]) if state else None,
            "master_epoch": int(state["master_epoch"]) if state else None,
            "sqlite": store.connection_settings(),
        }
        return payload
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Describe the fail-closed SCORP V4 candidate runtime")
    parser.add_argument("--describe", action="store_true", help="open SQLite and print a JSON status record")
    parser.add_argument("--database-path", required=True, type=pathlib.Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--allowed-root", action="append", required=True, type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.describe:
        raise SystemExit("V4_RUNTIME_COMMAND_REQUIRED: use --describe")
    project_id = str(args.project_id or "").strip()
    if not project_id:
        raise SystemExit("PROJECT_ID_EMPTY")
    payload = describe(
        database_path=args.database_path,
        project_id=project_id,
        allowed_roots=args.allowed_root,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
