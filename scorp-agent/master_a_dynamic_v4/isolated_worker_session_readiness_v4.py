"""Safe V3 legacy session preflight; never adopt or contact browser sessions.

A pre-lifecycle V3 JSON document can contain hundreds of historical turns
and conversation->session aliases but NO physical session registry and NO
pre-submit evidence. Reconstructing two "ACTIVE WORKER" sessions from it
would be unsafe. This module returns *aggregate* counters only.

No browser, GitHub, network, project SQLite, login, navigation or send APIs.
Never expose raw paths, conversation URLs, sessions, turn IDs or prompts.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, asdict
import datetime as dt
import json
import subprocess
from pathlib import Path


_PROTOCOL = "scorp.chrome-use-driver/v1"
_REPORT = "scorp.r2-legacy-session-readiness/1"
_ROLES = ("MASTER", "WORKER", "DIAGNOSTIC", "UNKNOWN")
_MAX_JSON_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class LegacyWorkerReadiness:
    protocol: str = _REPORT
    status: str = "UNAVAILABLE"
    reason: str = "STATE_NOT_INSPECTED"
    conversations_total: int = 0
    turns_total: int = 0
    distinct_referenced_session_count: int = 0
    registry_field_present: bool = False
    registry_rows: int | None = None
    master_turns: int = 0
    worker_turns: int = 0
    other_or_unknown_role_turns: int = 0
    unknown_browser_io_turns: int = 0
    unknown_submit_edge_turns: int = 0
    explicit_submit_edge_true_turns: int = 0
    # Legacy saved state is not a physical browser observation. Never claim
    # identity, a safe-to-retry message, or an admitted worker from it.
    two_distinct_gpt_workers_authenticated: bool = False
    genuine_worker_2_session_admitted: bool = False
    original_master_replay_authorized: bool = False
    browser_send_authorized: bool = False
    browser_io_performed: bool = False
    state_file_modified: bool = False


def inspect_legacy_worker_readiness(raw: object) -> LegacyWorkerReadiness:
    """Interpret V3 dictionaries WITHOUT trusting them for live admission."""
    def blocked(reason: str) -> LegacyWorkerReadiness:
        return LegacyWorkerReadiness(status="BLOCKED", reason=reason)

    if not isinstance(raw, dict) or raw.get("protocol_version") != _PROTOCOL:
        return blocked("DRIVER_PROTOCOL_OR_ROOT_INVALID")
    turns = raw.get("turns")
    conv = raw.get("conversations")
    if not isinstance(turns, dict) or not isinstance(conv, dict):
        return blocked("LEGACY_DICTIONARIES_INVALID")
    sessions = raw.get("sessions")
    if "sessions" in raw and not isinstance(sessions, dict):
        return blocked("SESSION_REGISTRY_SCHEMA_INVALID")
    # Only validated scalar metadata are counted; nothing about a stored
    # session, turn or conversation URL is emitted to output.
    roles = Counter()
    browser_unknown = submit_unknown = submit_true = 0
    refs: set[str] = set()
    for row in conv.values():
        if isinstance(row, dict) and isinstance(row.get("session"), str):
            if row["session"].strip():
                refs.add(row["session"])
    for row in turns.values():
        if not isinstance(row, dict):
            roles["UNCLASSIFIED"] += 1
            browser_unknown += 1
            submit_unknown += 1
            continue
        r = row.get("role")
        roles[r if isinstance(r, str) and r in _ROLES else "UNCLASSIFIED"] += 1
        s = row.get("session")
        if isinstance(s, str) and s.strip():
            refs.add(s)
        if type(row.get("browser_io_started")) is not bool:
            browser_unknown += 1
        if type(row.get("submit_edge_crossed")) is not bool:
            submit_unknown += 1
        elif row["submit_edge_crossed"]:
            submit_true += 1
    has_registry = "sessions" in raw
    # Even explicit saved registry entries are not a host/browser observation
    # and are NEVER accepted as physical GPT Worker admission.
    reason = ("PERSISTED_SESSIONS_NOT_HOST_ATTESTED" if has_registry
              else "LEGACY_SESSION_REGISTRY_MISSING")
    return LegacyWorkerReadiness(
        status="HISTORICAL_METADATA_ONLY",
        reason=reason,
        conversations_total=len(conv),
        turns_total=len(turns),
        distinct_referenced_session_count=len(refs),
        registry_field_present=has_registry,
        registry_rows=len(sessions) if isinstance(sessions, dict) else None,
        master_turns=roles["MASTER"],
        worker_turns=roles["WORKER"],
        other_or_unknown_role_turns=sum(v for k, v in roles.items() if k not in ("MASTER", "WORKER")),
        unknown_browser_io_turns=browser_unknown,
        unknown_submit_edge_turns=submit_unknown,
        explicit_submit_edge_true_turns=submit_true,
    )


def inspect_legacy_worker_readiness_file(path: Path) -> LegacyWorkerReadiness:
    """Fail-closed read-only open: missing/malformed file is NOT admission."""
    if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
        return LegacyWorkerReadiness(status="UNAVAILABLE", reason="STATE_FILE_MISSING_OR_SYMLINKED")
    try:
        if path.stat().st_size > _MAX_JSON_BYTES:
            return LegacyWorkerReadiness(status="BLOCKED", reason="STATE_FILE_OVERSIZE")
        with path.open("r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        return inspect_legacy_worker_readiness(raw)
    except (OSError, UnicodeError, ValueError, TypeError):
        return LegacyWorkerReadiness(status="UNAVAILABLE", reason="STATE_FILE_UNREADABLE")


@dataclass(frozen=True)
class BrowserSessionInventory:
    status: str
    reason: str
    observed_session_objects: int | None = None
    distinct_gpt_workers_host_authenticated: bool = False
    browser_send_authorized: bool = False
    browser_io_performed: bool = False
    production_writes: bool = False


def inspect_chrome_use_session_inventory(
    executable: Path,
    *,
    runner=None,
    timeout_seconds: float = 15.0,
) -> BrowserSessionInventory:
    """Bounded, read-only native CLI session list; no identity inference.

    Local CLI may return session labels, URLs and metadata. Only the count
    from an ACTUAL JSON list/dict is emitted. Missing JSON field != one
    session (PowerShell @($null) is a known false-positive footgun).
    """
    def unavailable(reason: str) -> BrowserSessionInventory:
        return BrowserSessionInventory("UNAVAILABLE", reason)
    if (not isinstance(executable, Path)
        or executable.is_symlink()
        or not executable.is_file()):
        return unavailable("CLI_BINARY_MISSING_OR_SYMLINKED")
    if (type(timeout_seconds) not in (int, float)
        or timeout_seconds <= 0 or timeout_seconds > 20):
        return unavailable("BOUND_TIMEOUT_INVALID")
    run = runner if runner is not None else subprocess.run
    try:
        result = run(
            [str(executable), "--json", "session", "list"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            return unavailable("SESSION_LIST_FAILED")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("success") is False:
            return unavailable("SESSION_LIST_SCHEMA_INVALID")
        sessions = payload.get("sessions")
        if not isinstance(sessions, (dict, list)):
            data = payload.get("data")
            if isinstance(data, dict):
                sessions = data.get("sessions")
            elif isinstance(data, list):
                sessions = data
        if not isinstance(sessions, (dict, list)):
            return unavailable("SESSION_COUNT_UNPROVEN")
        return BrowserSessionInventory(
            "OBSERVED_SESSION_OBJECTS_ONLY",
            "CLI_OBJECT_COUNT_NOT_GPT_IDENTITY_PROOF",
            len(sessions),
        )
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return unavailable("SESSION_LIST_UNAVAILABLE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="No-send V3 historical Worker 2 admission preflight")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(r"C:\ScorpAgent\state-v3\active\chrome-use-driver-v3.json"),
    )
    parser.add_argument("--include-live-cli-sessions", action="store_true")
    parser.add_argument(
        "--chrome-use-exe",
        type=Path,
        default=Path(r"C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe"),
    )
    opts = parser.parse_args(argv)
    value = asdict(inspect_legacy_worker_readiness_file(opts.state_file))
    value["observed_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if opts.include_live_cli_sessions:
        value["live_cli_sessions"] = asdict(
            inspect_chrome_use_session_inventory(opts.chrome_use_exe)
        )
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
