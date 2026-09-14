"""Run a no-send diagnostic for the ChatGPT composer.

The command deliberately stops before ``click``.  It is intended to verify
the current candidate's ``fill`` and controlled-input repair path in a fresh,
already-authenticated Chrome Use session without creating a browser intent or
replaying an ambiguous submission.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import pathlib
import sys
from typing import Any


BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from chrome_use_actor_driver_v3 import (  # noqa: E402
    ChromeUseActorDriverV3,
    SendControlResolutionError,
    _sha,
)
from chrome_use_cli_v3 import ChromeUseCliV3  # noqa: E402


DEFAULT_EXECUTABLE = r"C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe"
ROOT_URL = "https://chatgpt.com/"
DEFAULT_MARKER = "SCORP_V4_FILL_ONLY_DIAGNOSTIC"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


async def run_fill_diagnostic(
    cli: Any,
    driver: ChromeUseActorDriverV3,
    *,
    session: str,
    turn_id: str,
    marker: str,
    cleanup: bool = True,
) -> dict[str, Any]:
    """Fill a harmless marker, repair controlled input if needed, and never click."""

    record: dict[str, Any] = {
        "format": "scorp-v4-browser-fill-diagnostic/1",
        "observed_at_utc": _now(),
        "session": str(session),
        "turn_id": str(turn_id),
        "marker_sha256": _sha(str(marker)),
        "marker_length": len(str(marker)),
        "submit_actions": 0,
        "click_performed": False,
    }
    cleanup_error: dict[str, str] | None = None
    try:
        await cli.run_json(session, "open", ROOT_URL, timeout_seconds=30)
        observed = await driver._get_url(session)
        record["opened_url"] = observed
        if observed != ROOT_URL:
            raise ValueError("FILL_DIAGNOSTIC_ROOT_URL_MISMATCH")
        editor_ref = await driver._editor_ref(session)
        record["editor_ref"] = editor_ref
        driver.bind_turn(turn_id, None)
        await cli.run_json(session, "fill", editor_ref, marker, timeout_seconds=30)
        try:
            send_ref = await driver._send_ref_after_input_repair(session, editor_ref, marker)
        except SendControlResolutionError as exc:
            record.update({
                "status": "BLOCKED_SEND_CONTROL_MISSING",
                "send_ref": None,
                "diagnostics": dict(exc.diagnostics),
                "error": _safe_error(exc),
                "key_event_repair": exc.diagnostics.get("key_event_repair", "NOT_USED"),
            })
            return record
        record.update({
            "status": "READY_TO_SEND_NO_CLICK",
            "send_ref": send_ref,
            "key_event_repair": "USED" if any(
                args[:1] == ["type"] and "--key-events" in args
                for _, args, *_ in getattr(cli, "calls", [])
            ) else "NOT_NEEDED",
            "diagnostics": {
                "send_control_ref": send_ref,
                "prompt_sha256": _sha(str(marker)),
            },
        })
        return record
    except Exception as exc:
        record.update({"status": "BLOCKED_DIAGNOSTIC_ERROR", "error": _safe_error(exc)})
        return record
    finally:
        if cleanup:
            try:
                await cli.run_json(session, "session", "stop", timeout_seconds=30)
            except Exception as exc:  # cleanup failure must remain visible, not hide the result
                cleanup_error = _safe_error(exc)
        if cleanup_error is not None:
            record["cleanup_error"] = cleanup_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SCORP V4 no-send ChatGPT composer diagnostic")
    parser.add_argument("--run-fill-only", action="store_true", help="required explicit gate; still never clicks or sends")
    parser.add_argument("--executable", default=DEFAULT_EXECUTABLE)
    parser.add_argument("--driver-state-path", required=True, type=pathlib.Path)
    parser.add_argument("--evidence-path", required=True, type=pathlib.Path)
    parser.add_argument("--session", default="scorp-v4-fill-diagnostic")
    parser.add_argument("--turn-id", default="fill-diagnostic-turn")
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    return parser


async def _run_cli(args: argparse.Namespace) -> int:
    if not args.run_fill_only:
        print(json.dumps({
            "status": "FILL_ONLY_GATE_REQUIRED",
            "reason": "pass --run-fill-only; this command still never clicks or sends",
        }, ensure_ascii=False))
        return 2
    executable = pathlib.Path(args.executable)
    if not executable.is_file():
        raise RuntimeError("CHROME_USE_EXECUTABLE_MISSING")
    args.driver_state_path.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    cli = ChromeUseCliV3(executable=str(executable))
    driver = ChromeUseActorDriverV3(cli, args.driver_state_path, timeout_seconds=30)
    evidence = await run_fill_diagnostic(
        cli,
        driver,
        session=str(args.session),
        turn_id=str(args.turn_id),
        marker=str(args.marker),
    )
    evidence["candidate_code_commit"] = "runtime-provided"
    evidence["executable"] = str(executable)
    args.evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))
    return 0 if evidence.get("status") == "READY_TO_SEND_NO_CLICK" else 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run_cli(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
