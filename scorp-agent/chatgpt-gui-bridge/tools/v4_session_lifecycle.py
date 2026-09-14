"""Inspect or retire one explicitly named V4 Chrome Use session.

This tool intentionally has no global cleanup mode.  It never invokes
``session prune`` or ``close --all``.  ``list`` is read-only; ``retire``
requires an exact candidate manifest binding before it mutates the driver
state, and ``--stop`` targets only the named session after lifecycle checks.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Any


BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3  # noqa: E402
from chrome_use_cli_v3 import ChromeUseCliV3  # noqa: E402
from v4_master_controller_runtime import validate_candidate_binding  # noqa: E402
from master_a_dynamic_v4.models import sha256_json  # noqa: E402


DEFAULT_EXECUTABLE = r"C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe"


class LifecycleCommandError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect or retire one SCORP V4 Chrome Use session")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("list", help="read lifecycle state only")
    show.add_argument("--driver-state-path", required=True, type=pathlib.Path)
    show.add_argument("--evidence-path", type=pathlib.Path)

    retire = sub.add_parser("retire", help="retire one named session or turn")
    retire.add_argument("--driver-state-path", required=True, type=pathlib.Path)
    retire.add_argument("--session")
    retire.add_argument("--turn-id")
    retire.add_argument("--reason", required=True)
    retire.add_argument("--stop", action="store_true", help="stop only the named Chrome Use session")
    retire.add_argument("--allow-persistent", action="store_true", help="explicitly permit stopping Master/Worker")
    retire.add_argument("--executable", default=DEFAULT_EXECUTABLE)
    retire.add_argument("--candidate-commit", required=True)
    retire.add_argument("--candidate-manifest", required=True, type=pathlib.Path)
    retire.add_argument("--manifest-sha256", required=True)
    retire.add_argument("--evidence-path", required=True, type=pathlib.Path)
    return parser


def _manifest_binding(args: argparse.Namespace) -> dict[str, str]:
    manifest = pathlib.Path(args.candidate_manifest)
    if not manifest.is_file():
        raise LifecycleCommandError("CANDIDATE_MANIFEST_MISSING")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleCommandError("CANDIDATE_MANIFEST_INVALID") from exc
    try:
        return validate_candidate_binding(
            manifest,
            candidate_commit=args.candidate_commit,
            manifest_sha256=args.manifest_sha256,
        )
    except (OSError, ValueError) as exc:
        raise LifecycleCommandError(str(exc)) from exc


def list_lifecycle(state_path: pathlib.Path) -> dict[str, Any]:
    driver = ChromeUseActorDriverV3(ChromeUseCliV3(), state_path)
    return {
        "format": "scorp-v4-session-lifecycle-report/1",
        "status": "READ_ONLY",
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "driver_state_path": str(state_path.resolve()),
        "sessions": driver.lifecycle_snapshot(),
        "global_cleanup": "FORBIDDEN",
    }


async def retire_lifecycle(args: argparse.Namespace, *, cli: Any | None = None) -> dict[str, Any]:
    if bool(args.session) == bool(args.turn_id):
        raise LifecycleCommandError("EXACTLY_ONE_SESSION_OR_TURN_REQUIRED")
    binding = _manifest_binding(args)
    client = cli or ChromeUseCliV3(executable=str(args.executable))
    driver = ChromeUseActorDriverV3(client, args.driver_state_path)
    if args.turn_id:
        result = await driver.retire_turn(
            args.turn_id,
            reason=args.reason,
            stop=bool(args.stop),
            allow_persistent=bool(args.allow_persistent),
        )
    else:
        result = await driver.retire_session(
            args.session,
            reason=args.reason,
            stop=bool(args.stop),
            allow_persistent=bool(args.allow_persistent),
        )
    return {
        "format": "scorp-v4-session-lifecycle-receipt/1",
        "status": str(result.get("status") or "UNKNOWN"),
        "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "driver_state_path": str(pathlib.Path(args.driver_state_path).resolve()),
        "candidate_binding": binding,
        "result": result,
        "global_cleanup": "FORBIDDEN",
    }


def write_evidence(path: pathlib.Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Write one atomic receipt and hash the canonical payload before the hash field."""

    record = dict(payload)
    record["artifact_sha256"] = sha256_json(record)
    path = pathlib.Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return record


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            result = list_lifecycle(args.driver_state_path)
        else:
            result = asyncio.run(retire_lifecycle(args))
    except (LifecycleCommandError, ValueError, OSError) as exc:
        result = {
            "format": "scorp-v4-session-lifecycle-receipt/1",
            "status": "BLOCKED",
            "reason": str(exc),
            "global_cleanup": "FORBIDDEN",
        }
        evidence_path = getattr(args, "evidence_path", None)
        if evidence_path is not None:
            result = write_evidence(evidence_path, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    evidence_path = getattr(args, "evidence_path", None)
    if evidence_path is not None:
        result = write_evidence(evidence_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
