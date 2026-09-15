"""Build a safe, uploadable handoff packet for an ordinary web GPT.

The packet is an explicit bridge between a Windows operator and a web chat. It
does not open a browser, execute a project task, or grant the web chat local
permissions. The only optional side effect is writing the requested packet
file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import sys
from typing import Any


FORMAT = "scorp-v4-web-gpt-packet/1"
UTC = dt.timezone.utc


def _now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _load_preflight_module():
    path = pathlib.Path(__file__).with_name("v4_local_preflight.py")
    spec = importlib.util.spec_from_file_location("v4_local_preflight", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("PREFLIGHT_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"INVALID_HANDOFF_JSON:{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"INVALID_HANDOFF_OBJECT:{path}")
    return value


def _validation_record(root: pathlib.Path) -> tuple[pathlib.Path, dict[str, Any]]:
    """Load the newest local validation record, with legacy compatibility.

    The Fast Runtime command-core record is the authoritative candidate record
    once it exists.  The older Git6 record remains a fixture/compatibility
    source for repositories that have not migrated yet.
    """

    candidates = (
        root / "docs" / "handoffs" / "SCORP_V4_FAST_RUNTIME_COMMAND_CORE_VALIDATION.json",
        root / "docs" / "handoffs" / "SCORP_V4_GIT6_VALIDATION.json",
    )
    for path in candidates:
        if path.is_file():
            return path, _read_json(path)
    raise RuntimeError("VALIDATION_RECORD_MISSING")


def _validated_commit(validation: dict[str, Any]) -> str:
    """Return the candidate identity used by either validation schema."""

    return str(
        validation.get("validated_commit")
        or validation.get("implementation_commit")
        or validation.get("code_candidate_commit")
        or ""
    ).strip().lower()


def _current_live_gate(validation: dict[str, Any], candidate: str) -> dict[str, Any]:
    """Normalize the legacy and Fast Runtime live-gate shapes."""

    legacy = validation.get("current_candidate_live_gate")
    if isinstance(legacy, dict):
        return legacy
    verification = validation.get("verification")
    if not isinstance(verification, dict):
        return {"candidate_code_commit": candidate, "status": "NOT_RECORDED"}
    live = verification.get("live_verified")
    if not isinstance(live, dict):
        return {"candidate_code_commit": candidate, "status": "NOT_RECORDED"}
    result = str(live.get("result") or "NOT_RECORDED").strip().upper()
    status = {
        "PASS": "PASS",
        "BLOCKED": "BLOCKED_EXTERNAL_PRECONDITION",
        "FAIL": "FAIL",
    }.get(result, result)
    gate: dict[str, Any] = {
        "candidate_code_commit": candidate,
        "status": status,
        "reason": live.get("reason"),
        "evidence_path": live.get("evidence_path"),
        "retry_count_after_ambiguity": 0,
        "rule": "Do not retry until read-only browser evidence shows the blocker cleared.",
    }
    rate_limit = live.get("rate_limit_recovery_preflight")
    if isinstance(rate_limit, dict):
        gate["rate_limit_recovery_preflight"] = rate_limit
    return gate


def _prompt(candidate: str, status: str) -> str:
    return f"""You are the SCORP V4 planning and evidence-review GPT, not a Windows executor.

This packet was produced for candidate commit {candidate}. Its current packet status is {status}.
Read the packet and the repository handoff documents before making any claim.

Hard boundary:
- A Git repository does not grant this chat access to Windows, Python, SQLite, Chrome, or browser sessions.
- Treat WEB_GPT_DIRECT_LOCAL_CONTROL_UNAVAILABLE as authoritative unless a separate local connector provides current evidence.
- Do not ask for or store API keys, cookies, passwords, CAPTCHA answers, or login data.
- Do not claim that a plan ran, a Worker chat was created, or a project is complete without a current evidence record.

Return exactly these sections:
1. CAPABILITY: web_only, operator_handoff, sqlite_monitor, or browser_connected.
2. PLAN: one root_contract, acceptance_contract, and dependency-aware plan with at most two Worker assignments.
3. REQUIRED_LOCAL_ACTION: the next safe command or state transition for the Windows operator, or BLOCKED with the precise blocker.
4. ACCEPTANCE: PASS, FAIL, BLOCKED, or NOT_RUN for each criterion, each bound to candidate commit, command/action, observed state, raw output reference, and artifact hash.

If the packet is BLOCKED, stop at the blocker. Do not convert BLOCKED or NOT_RUN into PASS.
"""


def build_packet(
    repo_root: str | pathlib.Path,
    *,
    database_path: str | pathlib.Path | None = None,
    allowed_root: str | pathlib.Path | None = None,
    python_executable: str | pathlib.Path | None = None,
    chrome_executable: str | pathlib.Path | None = None,
    driver_state_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    root = pathlib.Path(repo_root).resolve()
    handoff_path = root / "docs" / "handoffs" / "SCORP_V4_WEB_GPT_HANDOFF.json"
    validation_path, validation = _validation_record(root)
    handoff = _read_json(handoff_path)
    validated = _validated_commit(validation)
    declared = str(handoff.get("candidate_commit", ""))
    blockers: list[dict[str, Any]] = []
    if not validated or declared != validated:
        blockers.append(
            {
                "code": "CANDIDATE_VERSION_MISMATCH",
                "declared_candidate": declared,
                "validated_candidate": validated,
            }
        )

    preflight = _load_preflight_module().build_report(
        root,
        python_executable=python_executable,
        database_path=database_path,
        allowed_root=allowed_root,
        chrome_executable=chrome_executable,
        driver_state_path=driver_state_path,
    )
    blockers.extend(preflight.get("blockers", []))
    status = "READY" if not blockers else "BLOCKED"
    current_live_gate = _current_live_gate(validation, validated or declared)
    validation_record = validation_path.relative_to(root).as_posix()
    return {
        "format": FORMAT,
        "generated_at": _now(),
        "target_repository": handoff.get("target_repository", "Scorp96/6"),
        "candidate_commit": validated or declared,
        "status": status,
        "authority": handoff.get("authority", {}),
        "evidence_binding": {
            "validation_record": validation_record,
            "handoff_record": "docs/handoffs/SCORP_V4_WEB_GPT_HANDOFF.json",
            "candidate_manifest": handoff.get("evidence_binding", {}).get("candidate_manifest"),
        },
        # Keep the transient browser gate in the single-file packet so an
        # ordinary web GPT does not mistake a ready-to-upload handoff for a
        # live-browser acceptance result.
        "current_live_gate": current_live_gate,
        "preflight": preflight,
        "blockers": blockers,
        "next_action": (
            "Upload this packet to the ordinary web GPT; it must remain in planning/review mode until a local operator returns current evidence."
            if status == "READY"
            else "Resolve the listed blocker locally, regenerate this packet, and do not ask the web GPT to infer missing capability."
        ),
        "prompt": _prompt(validated or declared, status),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only SCORP V4 web-GPT handoff packet")
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--python", dest="python_executable")
    parser.add_argument("--database-path", type=pathlib.Path)
    parser.add_argument("--allowed-root", type=pathlib.Path)
    parser.add_argument("--chrome-executable")
    parser.add_argument("--driver-state-path", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, help="Optional packet JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    packet = build_packet(
        args.repo_root,
        python_executable=args.python_executable,
        database_path=args.database_path,
        allowed_root=args.allowed_root,
        chrome_executable=args.chrome_executable,
        driver_state_path=args.driver_state_path,
    )
    encoded = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0 if packet["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
