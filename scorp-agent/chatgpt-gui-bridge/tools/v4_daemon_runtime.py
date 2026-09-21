"""Run the V4 local daemon against an existing SQLite state database.

This entrypoint is intentionally monitor-only by default: it reads the durable
snapshot, journals the ActivationArbiter decision and reports blockers. Browser
sends remain owned by fenced adapters. An explicit ``--active-controller`` gate
may attach the existing Master A controller in later runtime construction.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import uuid
import sys

BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3  # noqa: E402
from chrome_use_cli_v3 import ChromeUseCliV3  # noqa: E402
from master_a_dynamic_v4.acceptance import AcceptanceValidator  # noqa: E402
from master_a_dynamic_v4.execution_adapter import LocalExecutionAdapter  # noqa: E402
from master_a_dynamic_v4.git_worktree import GitWorktreeManager  # noqa: E402
from master_a_dynamic_v4.daemon import (  # noqa: E402
    LocalDaemon,
    MasterSupervisorActionHandler,
    PersistentControllerActionHandler,
)
from master_a_dynamic_v4.master_controller import MasterAController  # noqa: E402
from master_a_dynamic_v4.master_supervisor import MasterSupervisor  # noqa: E402
from master_a_dynamic_v4.master_reasoning import MasterReasoningCoordinator  # noqa: E402
from master_a_dynamic_v4.path_policy import PathPolicy  # noqa: E402
from master_a_dynamic_v4.scheduler import Scheduler, WorkerFenceError  # noqa: E402
from master_a_dynamic_v4.state_store import StateStore  # noqa: E402
from v4_bridge_gateway import V4BridgeGateway  # noqa: E402
from tools.v4_master_controller_runtime import (  # noqa: E402
    DEFAULT_EXECUTABLE,
    _worker_prompt,
    parse_structured_response,
)
from v4_auth import probe_chatgpt_auth  # noqa: E402
from v4_browser_engine import build_v4_browser_engine  # noqa: E402
from v4_physical_rebind import ReadOnlyBrowserRebinder  # noqa: E402


class MonitorOnlyEngine:
    """Engine used by the optional MasterSupervisor attachment.

    It deliberately provides no submit or reconcile operation. Physical
    rebind must be supplied by a separate read-only adapter.
    """

    def auth_state(self, _channel: str) -> dict[str, str]:
        return {"status": "MONITOR_ONLY"}

    def submit(self, _intent):
        raise RuntimeError("MONITOR_ONLY_BROWSER_SUBMIT_FORBIDDEN")

    def reconcile(self, _intent):
        raise RuntimeError("MONITOR_ONLY_BROWSER_RECONCILE_FORBIDDEN")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SCORP V4 local daemon")
    parser.add_argument("--database-path", required=True, type=pathlib.Path)
    parser.add_argument("--allowed-root", required=True, type=pathlib.Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--daemon-epoch",
        required=False,
        type=int,
        help="Optional expected epoch; omit to acquire the current SQLite epoch.",
    )
    parser.add_argument(
        "--actor-id",
        default=None,
        help="Explicit durable owner id. Omit for a unique physical-process owner.",
    )
    parser.add_argument("--daemon-ttl-seconds", type=int, default=30)
    parser.add_argument("--health-path", type=pathlib.Path)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--supervise-master", action="store_true")
    parser.add_argument("--master-session-id", default="master-a-runtime")
    parser.add_argument(
        "--active-controller",
        action="store_true",
        help="explicitly enable the fenced MasterAController action adapter",
    )
    parser.add_argument("--driver-state-path", type=pathlib.Path)
    return parser


def _resolve_daemon_actor_id(raw: object | None) -> str:
    explicit = str(raw or "").strip()
    if explicit:
        return explicit
    # A daemon lease fences a physical runtime process, not merely a logical
    # Scheduled Task name.  Reusing a fixed owner across process restarts can
    # renew a still-live lease and illegally reuse its daemon epoch.
    return f"scorp-daemon-{os.getpid()}-{uuid.uuid4().hex}"


def _validate_active_controller_options(args: argparse.Namespace) -> None:
    """Fail before any browser-capable runtime can be built."""
    if not bool(getattr(args, "active_controller", False)):
        return
    if getattr(args, "driver_state_path", None) is None:
        raise RuntimeError("DRIVER_STATE_PATH_REQUIRED_FOR_ACTIVE_CONTROLLER")


def _build_persistent_action_handlers(
    controller,
    gateway,
    *,
    worker_prompt_factory,
    reasoning_coordinator=None,
):
    """Map daemon actions to fenced deterministic and reasoning seams."""
    reasoning_callback = (
        reasoning_coordinator.run_once
        if reasoning_coordinator is not None
        else None
    )
    handler = PersistentControllerActionHandler(
        controller,
        worker_prompt_factory=worker_prompt_factory,
        recover_callback=gateway.recover,
        reasoning_callback=reasoning_callback,
    )
    return {
        "RECONCILE_AMBIGUOUS": handler,
        "ASSIGN_WORKER": handler,
        "WAKE_MASTER": handler,
        "RESUME_WORKER": handler,
        "RECOVER_STALLED": handler,
        "REASON_MASTER": handler,
    }



def _build_active_controller_runtime(
    database_path: pathlib.Path,
    project_id: str,
    allowed_root: pathlib.Path,
    driver_state_path: pathlib.Path,
    master_session_id: str,
):
    """Build the existing fenced browser/controller stack without performing I/O."""
    executable = pathlib.Path(DEFAULT_EXECUTABLE)
    if not executable.is_file():
        raise RuntimeError("CHROME_USE_EXECUTABLE_MISSING")
    state_path = pathlib.Path(driver_state_path).resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    cli = ChromeUseCliV3(executable=str(executable))
    driver = ChromeUseActorDriverV3(cli, state_path, timeout_seconds=120)

    async def auth_probe(channel: str):
        session = "scorp-v4-daemon-auth-" + hashlib.sha256(str(project_id).encode()).hexdigest()[:12]
        try:
            await cli.run_json(session, "open", "https://chatgpt.com/", timeout_seconds=30)
            return await probe_chatgpt_auth(cli, driver, session, channel)
        except Exception as exc:
            return {"status": "AUTH_PROBE_FAILED", "channel": channel, "error": type(exc).__name__}

    engine = build_v4_browser_engine(
        driver,
        auth_probe=auth_probe,
        response_parser=parse_structured_response,
        timeout_seconds=120,
    )
    gateway = V4BridgeGateway(
        database_path,
        str(project_id),
        [allowed_root],
        engine,
        master_ttl_seconds=1500,
    )
    controller = MasterAController(
        gateway,
        str(master_session_id),
        execution_adapter=LocalExecutionAdapter(
            [allowed_root],
            python_executable=sys.executable,
            pythonpath=AGENT_ROOT,
            allowed_modules=["master_a_dynamic_v4.csv_workload.cli"],
        ),
        git_worktree_manager=GitWorktreeManager([allowed_root]),
    )
    controller.attach_existing_session()
    rebind_callback = ReadOnlyBrowserRebinder(
        store=gateway.store,
        browser_adapter=gateway.adapter,
        driver=driver,
        auth_probe=auth_probe,
        project_id=str(project_id),
        channel="master",
        actor_id="A",
        timeout_seconds=120,
    )
    return gateway, controller, rebind_callback

def run_runtime(args: argparse.Namespace) -> int:
    database_path = pathlib.Path(args.database_path).resolve()
    if not database_path.is_file():
        raise RuntimeError("STATE_DATABASE_MISSING")
    allowed_root = pathlib.Path(args.allowed_root).resolve(strict=True)
    if args.daemon_epoch is not None and int(args.daemon_epoch) < 0:
        raise RuntimeError("DAEMON_EPOCH_INVALID")
    if int(args.daemon_ttl_seconds) <= 0:
        raise RuntimeError("DAEMON_TTL_INVALID")
    if float(args.interval_seconds) < 0:
        raise RuntimeError("DAEMON_INTERVAL_INVALID")
    if not args.forever and int(args.max_iterations) <= 0:
        raise RuntimeError("DAEMON_ITERATION_BOUND_INVALID")
    _validate_active_controller_options(args)
    actor_id = _resolve_daemon_actor_id(getattr(args, "actor_id", None))
    health_path = pathlib.Path(args.health_path).resolve() if args.health_path else database_path.with_suffix(".daemon-health.json")

    store = StateStore(database_path, [allowed_root])
    scheduler = Scheduler(store, str(args.project_id), PathPolicy([allowed_root]), max_workers=2)
    controller_gateway = None
    lease = None
    graceful_exit = False
    run_loop_started = False
    try:
        recovery_gate = store.daemon_recovery_gate(str(args.project_id))
        if recovery_gate["status"] != "ALLOWED":
            raise RuntimeError(
                f"DAEMON_RECOVERY_{recovery_gate['status']}:{recovery_gate.get('reason', '')}"
            )
        # Build all local controller, adapter, and supervisor objects before
        # acquiring the short-lived daemon authority.  Construction opens
        # SQLite-backed adapters but performs no browser I/O; holding the
        # lease across it let a slow Chrome Use startup expire the lease
        # before LocalDaemon's heartbeat thread could begin.
        action_handlers = {}
        master_supervision = False
        controller = None
        rebind_callback = None
        reasoning_coordinator = None
        if args.active_controller:
            controller_gateway, controller, rebind_callback = _build_active_controller_runtime(
                database_path,
                str(args.project_id),
                allowed_root,
                pathlib.Path(args.driver_state_path),
                str(args.master_session_id),
            )
            reasoning_coordinator = MasterReasoningCoordinator(
                controller_gateway,
                controller,
            )
            action_handlers.update(
                _build_persistent_action_handlers(
                    controller,
                    controller_gateway,
                    worker_prompt_factory=_worker_prompt,
                    reasoning_coordinator=reasoning_coordinator,
                )
            )
        if args.supervise_master:
            if controller is None:
                controller_gateway = V4BridgeGateway(
                    database_path,
                    str(args.project_id),
                    [allowed_root],
                    MonitorOnlyEngine(),
                    master_ttl_seconds=max(60, int(args.daemon_ttl_seconds)),
                )
                controller = MasterAController(controller_gateway, str(args.master_session_id))
                controller.attach_existing_session()
            physical_health_probe = (
                rebind_callback.health_probe
                if rebind_callback is not None
                and callable(getattr(rebind_callback, "health_probe", None))
                else None
            )
            supervisor = MasterSupervisor(
                controller,
                rebind_callback=rebind_callback,
                physical_health_probe=physical_health_probe,
            )
            supervisor_handler = MasterSupervisorActionHandler(supervisor)
            action_handlers.update(
                {
                    "RESUME_MASTER": supervisor_handler,
                    "HEARTBEAT_IDLE": supervisor_handler,
                }
            )
            master_supervision = True

        # The lease now fences only the runnable daemon loop and its external
        # actions.  This keeps the TTL independent of local initialization
        # time while preserving the existing epoch and owner checks.
        lease = store.acquire_daemon_lease(
            str(args.project_id),
            actor_id,
            ttl_seconds=int(args.daemon_ttl_seconds),
        )
        if args.daemon_epoch is not None and int(lease["daemon_epoch"]) != int(args.daemon_epoch):
            raise RuntimeError(
                f"DAEMON_EPOCH_MISMATCH expected={args.daemon_epoch} actual={lease['daemon_epoch']}"
            )
        daemon_epoch = int(lease["daemon_epoch"])
        daemon = LocalDaemon(
            store,
            project_id=str(args.project_id),
            daemon_epoch=daemon_epoch,
            snapshot_provider=lambda: dataclasses.replace(
                store.activation_snapshot(
                    str(args.project_id), daemon_epoch=daemon_epoch
                ),
                reasoning_required=bool(
                    reasoning_coordinator is not None
                    and reasoning_coordinator.reasoning_required()
                ),
            ),
            lease_heartbeat=lambda: store.heartbeat_daemon_lease(
                str(args.project_id),
                actor_id,
                daemon_epoch=daemon_epoch,
                ttl_seconds=int(args.daemon_ttl_seconds),
            ),
            worker_lease_recovery=lambda: scheduler.recover_expired_leases(),
            worker_lease_renewal=lambda: _renew_active_workers(
                store, scheduler, str(args.project_id),
                lease_seconds=max(30, int(args.daemon_ttl_seconds) * 3),
            ),
            project_completion=lambda: _finalize_project_if_accepted(
                store, str(args.project_id)
            ),
            recovery_callback=lambda: store.record_daemon_recovery(
                str(args.project_id)
            ),
            action_handlers=action_handlers,
            health_path=health_path,
            actor_id=actor_id,
            action_heartbeat_interval_seconds=max(
                0.5,
                min(5.0, float(args.daemon_ttl_seconds) / 3.0),
            ),
        )
        run_loop_started = True
        decisions = daemon.run_loop(
            interval_seconds=float(args.interval_seconds),
            max_iterations=None if args.forever else int(args.max_iterations),
        )
        health = json.loads(health_path.read_text(encoding="utf-8"))
        summary = {
            "format": "scorp-v4-daemon-run/1",
            "status": health["status"],
            "project_id": str(args.project_id),
            "daemon_epoch": daemon_epoch,
            "decision_count": len(decisions),
            "decisions": [decision.as_dict() for decision in decisions],
            "health_path": str(health_path),
            "browser_send": "ENABLED_FENCED" if args.active_controller else "FORBIDDEN",
            "master_supervision": master_supervision,
            "active_controller": bool(args.active_controller),
            "persistent_master_reasoning": bool(reasoning_coordinator is not None),
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        graceful_exit = True
        return 0 if health["status"] in {"HEALTHY", "TERMINAL"} else 2
    finally:
        # A lease acquired during startup must not be stranded when a
        # configuration fence (for example a stale explicit epoch) rejects
        # the process before it can execute a daemon loop.  Such a rejection
        # is not a crash and must not consume restart budget.  Once the loop
        # has started, unexpected exceptions deliberately retain the lease
        # until expiry so the supervisor can classify them as a crash.
        if lease is not None and (graceful_exit or not run_loop_started):
            store.release_daemon_lease(
                str(args.project_id),
                actor_id,
                daemon_epoch=int(lease["daemon_epoch"]),
            )
        if controller_gateway is not None:
            controller_gateway.close()
        store.close()


def _finalize_project_if_accepted(store, project_id: str) -> dict[str, object]:
    """Terminalize only a release candidate that passes current acceptance."""
    state = store.get_project_state(project_id)
    if str(state.get("status") or "") in {"COMPLETE", "HARD_BLOCKED"}:
        return {"status": "TERMINAL", "project_status": str(state.get("status"))}
    candidate = str(state.get("completion_candidate_commit") or "").strip().lower()
    if not candidate:
        return {"status": "NOT_READY", "reason": "RELEASE_CANDIDATE_MISSING"}
    decision = AcceptanceValidator(store).finalize(project_id, candidate, {})
    return {"status": decision.status.value, "blockers": list(decision.blockers)}


def _renew_active_workers(store, scheduler, project_id: str, *, lease_seconds: int) -> None:
    """Renew only currently fenced claims; expired claims are left for recovery."""
    state = store.get_project_state(project_id)
    claims = scheduler.load_active_claims(master_epoch=int(state["master_epoch"]))
    for claim in claims:
        try:
            scheduler.renew_worker_lease(
                claim,
                master_epoch=int(state["master_epoch"]),
                lease_seconds=int(lease_seconds),
            )
        except WorkerFenceError:
            # Recovery on the next observation will reclaim a concurrently
            # expired claim.  Do not renew with a stale token or epoch.
            continue


def main(argv: list[str] | None = None) -> int:
    return run_runtime(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
