"""Run the V4 Master A monitor as a bounded, auditable local process.

This entrypoint owns no planning and never sends a ChatGPT prompt.  It reads an
existing SQLite state database, renews the durable Master lease, and stops on a
terminal state or an unresolved physical browser rebind.  ``--rebind`` enables
the read-only browser snapshot/rebind adapter; it still never fills a composer
or clicks Send.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
from collections.abc import Callable, Mapping
from typing import Any


BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from master_a_dynamic_v4.master_controller import MasterAController  # noqa: E402
from master_a_dynamic_v4.master_supervisor import (  # noqa: E402
    MasterSupervisor,
    SupervisorDecision,
    SupervisorLoopResult,
)
from v4_bridge_gateway import V4BridgeGateway  # noqa: E402


DEFAULT_EXECUTABLE = r"C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe"
UTC = dt.timezone.utc


def _utc_now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MonitorOnlyEngine:
    """Prevent accidental browser submission while the monitor is attached."""

    def auth_state(self, _channel: str) -> dict[str, str]:
        return {"status": "MONITOR_ONLY"}

    def submit(self, _intent: Mapping[str, Any]) -> dict[str, str]:
        raise RuntimeError("MONITOR_ONLY_BROWSER_SUBMIT_FORBIDDEN")

    def reconcile(self, _intent: Mapping[str, Any]) -> dict[str, str]:
        raise RuntimeError("MONITOR_ONLY_BROWSER_RECONCILE_FORBIDDEN")


class DecisionJournal:
    """Append one fsynced JSON record for every supervisor decision."""

    FORMAT = "scorp-v4-master-supervisor-decision/1"

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path).resolve()

    def append(self, decision: Any) -> dict[str, Any]:
        if not dataclasses.is_dataclass(decision):
            raise ValueError("SUPERVISOR_DECISION_INVALID")
        try:
            payload = dataclasses.asdict(decision)
        except (TypeError, ValueError) as exc:
            raise ValueError("SUPERVISOR_DECISION_INVALID") from exc
        record = {
            "format": self.FORMAT,
            "recorded_at": _utc_now(),
            **payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return record


def run_supervisor(
    controller: Any,
    *,
    rebind_callback: Callable[[Mapping[str, Any]], Any] | None = None,
    decision_log: str | pathlib.Path | None = None,
    interval_seconds: float = 30.0,
    max_iterations: int | None = 1,
    stop_event: Any | None = None,
    sleep: Callable[[float], Any] | None = None,
) -> SupervisorLoopResult:
    """Run the durable monitor and journal every decision before continuing."""

    journal = DecisionJournal(decision_log) if decision_log is not None else None

    def on_decision(decision: Any) -> None:
        if journal is not None:
            journal.append(decision)

    supervisor = MasterSupervisor(controller, rebind_callback=rebind_callback)
    return supervisor.run_loop(
        interval_seconds=interval_seconds,
        max_iterations=max_iterations,
        stop_event=stop_event,
        sleep=sleep or __import__("time").sleep,
        on_decision=on_decision,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SCORP V4 Master A local monitor")
    parser.add_argument("--database-path", required=True, type=pathlib.Path)
    parser.add_argument("--allowed-root", required=True, type=pathlib.Path)
    parser.add_argument("--project-id", default="scorp-v4-master-runtime")
    parser.add_argument("--session-id", default="master-a-runtime")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--forever", action="store_true", help="keep polling until stopped or blocked")
    parser.add_argument("--decision-log", type=pathlib.Path)
    parser.add_argument("--master-ttl-seconds", type=int, default=1500)
    parser.add_argument("--rebind", action="store_true", help="enable read-only physical browser rebind")
    parser.add_argument("--driver-state-path", type=pathlib.Path)
    parser.add_argument("--executable", default=DEFAULT_EXECUTABLE)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    return parser


def _auth_probe(cli: Any, project_id: str):
    async def probe(channel: str) -> dict[str, Any]:
        session = "scorp-v4-master-auth-" + hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:12]
        try:
            await cli.run_json(session, "open", "https://chatgpt.com/", timeout_seconds=30)
            page = await cli.run_json(session, "read", timeout_seconds=30)
        except Exception as exc:
            return {"status": "AUTH_PROBE_FAILED", "channel": channel, "error": type(exc).__name__}
        text = json.dumps(page, ensure_ascii=False)
        lowered = text.casefold()
        if any(token in lowered for token in ("登录", "log in", "sign up", "captcha", "验证码")):
            return {"status": "AUTHENTICATION_REQUIRED", "channel": channel}
        if "plus" not in lowered and "准备好了" not in text and "ready" not in lowered:
            return {"status": "AUTH_PROBE_UNCERTAIN", "channel": channel}
        return {"status": "AUTHENTICATED", "channel": channel}

    return probe


def _build_rebind_callback(args: argparse.Namespace, gateway: V4BridgeGateway):
    if not args.rebind:
        return None
    executable = pathlib.Path(args.executable)
    if not executable.is_file():
        raise RuntimeError("CHROME_USE_EXECUTABLE_MISSING")
    if args.driver_state_path is None:
        raise RuntimeError("DRIVER_STATE_PATH_REQUIRED_FOR_REBIND")

    from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
    from chrome_use_cli_v3 import ChromeUseCliV3
    from v4_physical_rebind import ReadOnlyBrowserRebinder

    cli = ChromeUseCliV3(executable=str(executable))
    driver = ChromeUseActorDriverV3(
        cli,
        pathlib.Path(args.driver_state_path).resolve(),
        timeout_seconds=args.timeout_seconds,
    )
    return ReadOnlyBrowserRebinder(
        store=gateway.store,
        browser_adapter=gateway.adapter,
        driver=driver,
        auth_probe=_auth_probe(cli, str(args.project_id)),
        project_id=str(args.project_id),
        channel="master",
        actor_id="A",
        timeout_seconds=args.timeout_seconds,
    )


def run_runtime(args: argparse.Namespace) -> int:
    database_path = pathlib.Path(args.database_path).resolve()
    if not database_path.is_file():
        raise RuntimeError("STATE_DATABASE_MISSING")
    allowed_root = pathlib.Path(args.allowed_root).resolve(strict=True)
    if float(args.interval_seconds) < 0:
        raise RuntimeError("SUPERVISOR_INTERVAL_INVALID")
    if not args.forever and int(args.max_iterations) <= 0:
        raise RuntimeError("SUPERVISOR_ITERATION_BOUND_INVALID")
    decision_log = pathlib.Path(args.decision_log).resolve() if args.decision_log else database_path.with_suffix(".supervisor.jsonl")

    gateway = V4BridgeGateway(
        database_path,
        str(args.project_id),
        [allowed_root],
        MonitorOnlyEngine(),
        master_ttl_seconds=int(args.master_ttl_seconds),
    )
    try:
        controller = MasterAController(gateway, str(args.session_id))
        attached = controller.attach_existing_session()
        rebind_callback = _build_rebind_callback(args, gateway)
        result = run_supervisor(
            controller,
            rebind_callback=rebind_callback,
            decision_log=decision_log,
            interval_seconds=float(args.interval_seconds),
            max_iterations=None if args.forever else int(args.max_iterations),
        )
        summary = {
            "format": "scorp-v4-master-supervisor-run/1",
            "status": result.status,
            "stop_reason": result.stop_reason,
            "decision_count": len(result.decisions),
            "project_id": str(args.project_id),
            "session_id": str(args.session_id),
            "attached": attached,
            "rebind_enabled": bool(args.rebind),
            "browser_send": "FORBIDDEN",
            "decision_log": str(decision_log),
            "decisions": [dataclasses.asdict(item) for item in result.decisions],
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if result.status in {"MASTER_ACTIVE", "TERMINAL"} else 2
    finally:
        gateway.close()


def main(argv: list[str] | None = None) -> int:
    return run_runtime(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
