"""Read-only preflight for handing SCORP V4 to an ordinary web GPT.

The report deliberately separates repository readiness from local-control
readiness. Reading this repository in a web chat never grants that chat access
to Windows, SQLite, Chrome, or a local Python process.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import sys
from collections.abc import Mapping
from typing import Any


FORMAT = "scorp-v4-local-preflight/1"
UTC = dt.timezone.utc


def _now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resolve_executable(value: str | pathlib.Path | None) -> pathlib.Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = pathlib.Path(text)
    if candidate.is_file():
        return candidate.resolve()
    found = shutil.which(text)
    return pathlib.Path(found).resolve() if found else None


def _capability(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def build_report(
    repo_root: str | pathlib.Path,
    *,
    python_executable: str | pathlib.Path | None = None,
    database_path: str | pathlib.Path | None = None,
    allowed_root: str | pathlib.Path | None = None,
    chrome_executable: str | pathlib.Path | None = None,
    driver_state_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    root = pathlib.Path(repo_root).resolve()
    required = [
        "GPT_START_HERE.md",
        "scripts/run-candidate-validation.ps1",
        "scorp-agent/master_a_dynamic_v4",
        "scorp-agent/chatgpt-gui-bridge/tools/v4_master_controller_runtime.py",
        "scorp-agent/chatgpt-gui-bridge/tools/v4_master_supervisor_runtime.py",
        "scorp-agent/chatgpt-gui-bridge/tools/v4_web_gpt_packet.py",
    ]
    present = {item: (root / item).exists() for item in required}
    missing = [item for item, exists in present.items() if not exists]
    python_path = _resolve_executable(python_executable or sys.executable)
    database = pathlib.Path(database_path).resolve() if database_path else None
    allowed = pathlib.Path(allowed_root).resolve() if allowed_root else None
    chrome = _resolve_executable(chrome_executable)
    driver_state = pathlib.Path(driver_state_path).resolve() if driver_state_path else None

    blockers: list[dict[str, Any]] = []
    if missing:
        blockers.append({"code": "MISSING_REPOSITORY_FILES", "paths": missing})
    if python_path is None:
        blockers.append({"code": "PYTHON_RUNTIME_MISSING"})

    offline_ready = not missing and python_path is not None
    monitor_ready = offline_ready and database is not None and database.is_file() and allowed is not None and allowed.is_dir()
    browser_ready = chrome is not None and driver_state is not None and driver_state.exists()
    capabilities = {
        "web_gpt_direct_local_control": _capability(
            "UNAVAILABLE",
            "A web GPT cannot gain Windows or SQLite permissions by reading Git alone.",
            required_transport="local_host_connector_or_operator_handoff",
        ),
        "git_handoff": _capability(
            "MANUAL_OR_EXTERNAL_POLLING",
            "Git can carry a structured plan and evidence, but this candidate does not make Git itself a local executor.",
            authority="SQLite_when_running_locally",
        ),
        "offline_validation": _capability(
            "READY" if offline_ready else "BLOCKED",
            "Repository entrypoints and a Python runtime are present." if offline_ready else "Repository or Python prerequisites are missing.",
            command="scripts/run-candidate-validation.ps1",
        ),
        "sqlite_master_monitor": _capability(
            "READY" if monitor_ready else "NOT_CONFIGURED",
            "An existing SQLite database and allowed root were supplied." if monitor_ready else "Supply an existing database and allowed root; the monitor will not create project state.",
            command="scorp-agent/chatgpt-gui-bridge/tools/v4_master_supervisor_runtime.py",
        ),
        "browser_rebind": _capability(
            "READY_TO_REBIND" if browser_ready else "BLOCKED_MISSING_BROWSER_CONFIG",
            "Chrome Use executable and driver state are present." if browser_ready else "An existing Chrome Use executable and driver-state path are required; login and CAPTCHA are never bypassed.",
            executable=str(chrome) if chrome else None,
            driver_state=str(driver_state) if driver_state else None,
        ),
        "browser_submission": _capability(
            "EXPLICIT_OPERATOR_GATE",
            "Preflight never sends a prompt. Submission requires the separate operator-gated runtime and a fresh review.",
        ),
    }
    return {
        "format": FORMAT,
        "generated_at": _now(),
        "repo_root": str(root),
        "python_executable": str(python_path) if python_path else None,
        "required_paths": present,
        "database_path": str(database) if database else None,
        "allowed_root": str(allowed) if allowed else None,
        "capabilities": capabilities,
        "blockers": blockers,
        "overall_status": "READY" if not blockers else "BLOCKED",
        "handoff_rule": "Do not claim local execution, browser control, or completion unless the corresponding capability has current evidence.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only SCORP V4 local-interface preflight")
    parser.add_argument("--repo-root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--python", dest="python_executable")
    parser.add_argument("--database-path", type=pathlib.Path)
    parser.add_argument("--allowed-root", type=pathlib.Path)
    parser.add_argument("--chrome-executable")
    parser.add_argument("--driver-state-path", type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        args.repo_root,
        python_executable=args.python_executable,
        database_path=args.database_path,
        allowed_root=args.allowed_root,
        chrome_executable=args.chrome_executable,
        driver_state_path=args.driver_state_path,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["overall_status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
