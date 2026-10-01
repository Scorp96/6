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
import threading
from typing import Any

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
from master_a_dynamic_v4.runtime_commands import RuntimeCommandService  # noqa: E402
from master_a_dynamic_v4.runtime_pipe import RuntimePipeServer, create_listener  # noqa: E402
from master_a_dynamic_v4.runtime_pipe_cli import authkey_from_file  # noqa: E402
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
    parser.add_argument("--runtime-pipe-authkey-file", type=pathlib.Path)
    parser.add_argument("--runtime-pipe-actor", default="gpt-master")
    parser.add_argument("--stop-request-path", type=pathlib.Path)
    return parser


def _resolve_daemon_actor_id(raw: object | None) -> str:
    explicit = str(raw or "").strip()
    if explicit:
        return explicit
    # A daemon lease fences a physical runtime process, not merely a logical
    # Scheduled Task name.  Reusing a fixed owner across process restarts can
    # renew a still-live lease and illegally reuse its daemon epoch.
    return f"scorp-daemon-{os.getpid()}-{uuid.uuid4().hex}"


class FencedStopRequest:
    """Action-boundary stop signal fenced to one physical daemon identity."""

    def __init__(
        self,
        path: pathlib.Path,
        *,
        project_id: str,
        daemon_owner: str,
        daemon_epoch: int,
        pid: int,
    ) -> None:
        self.path = pathlib.Path(path).resolve()
        self.project_id = str(project_id)
        self.daemon_owner = str(daemon_owner)
        self.daemon_epoch = int(daemon_epoch)
        self.pid = int(pid)
        self.request_id: str | None = None
        self.rejection_reason: str | None = None
        self._accepted_payload: dict[str, Any] | None = None

    def _reject(self, reason: str, payload: dict[str, Any] | None = None) -> bool:
        self.rejection_reason = str(reason)
        rejection_path = self.path.with_suffix(".rejected.json")
        observed = payload or {}
        receipt = {
            "protocol_version": "scorp.v4.stop-rejection/1",
            "status": "REJECTED",
            "reason": self.rejection_reason,
            "expected_project_id": self.project_id,
            "expected_daemon_owner": self.daemon_owner,
            "expected_daemon_epoch": self.daemon_epoch,
            "expected_pid": self.pid,
            "observed_project_id": str(observed.get("project_id") or ""),
            "observed_daemon_owner": str(observed.get("daemon_owner") or ""),
            "observed_daemon_epoch": observed.get("daemon_epoch"),
            "observed_pid": observed.get("pid"),
            "observed_request_id": str(observed.get("request_id") or ""),
        }
        temporary = rejection_path.with_name(rejection_path.name + ".tmp")
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, rejection_path)
        return False

    def is_set(self) -> bool:
        if self._accepted_payload is not None:
            return True
        if not self.path.is_file():
            self.rejection_reason = None
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return self._reject("STOP_REQUEST_INVALID_JSON")
        if not isinstance(payload, dict):
            return self._reject("STOP_REQUEST_INVALID_OBJECT")
        request_id = str(payload.get("request_id") or "").strip()
        try:
            matches = (
                str(payload.get("project_id") or "") == self.project_id
                and str(payload.get("daemon_owner") or "") == self.daemon_owner
                and int(payload.get("daemon_epoch")) == self.daemon_epoch
                and int(payload.get("pid")) == self.pid
            )
        except (TypeError, ValueError):
            matches = False
        if not request_id:
            return self._reject("STOP_REQUEST_ID_MISSING", payload)
        if not matches:
            return self._reject("STOP_REQUEST_IDENTITY_MISMATCH", payload)
        self.request_id = request_id
        self.rejection_reason = None
        self._accepted_payload = dict(payload)
        return True

    def acknowledge(self) -> pathlib.Path:
        if self._accepted_payload is None or not self.request_id:
            raise RuntimeError("STOP_REQUEST_NOT_ACCEPTED")
        ack_path = self.path.with_suffix(".ack.json")
        payload = {
            "protocol_version": "scorp.v4.stop-ack/1",
            "status": "ACKNOWLEDGED",
            "project_id": self.project_id,
            "daemon_owner": self.daemon_owner,
            "daemon_epoch": self.daemon_epoch,
            "pid": self.pid,
            "request_id": self.request_id,
        }
        temporary = ack_path.with_name(ack_path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, ack_path)
        return ack_path


def _resolve_stop_request_path(
    raw: pathlib.Path,
    allowed_root: pathlib.Path,
    database_path: pathlib.Path,
) -> pathlib.Path:
    """Keep daemon control files inside the isolated authority directory."""

    path = pathlib.Path(raw).resolve()
    authority_root = pathlib.Path(database_path).resolve().parent
    workspace = pathlib.Path(allowed_root).resolve()
    try:
        workspace.relative_to(authority_root)
        path.relative_to(authority_root)
    except ValueError as exc:
        raise RuntimeError("STOP_REQUEST_PATH_OUTSIDE_AUTHORITY_ROOT") from exc
    return path


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

    def blocked_action(decision):
        return {
            "status": "BLOCKED",
            "reason": str(getattr(decision, "reason", "ACTION_BLOCKED") or "ACTION_BLOCKED"),
        }

    def emergency_stop_action(decision):
        # OperatorControl has already durably fenced the project.  The daemon
        # action is an explicit safe-stop acknowledgement; it must not wake
        # the controller or attempt any browser work.
        return {
            "status": "TERMINAL",
            "reason": str(getattr(decision, "reason", "OPERATOR_EMERGENCY_STOPPED") or "OPERATOR_EMERGENCY_STOPPED"),
        }

    def fence_stale_results_action(decision):
        callback = getattr(gateway, "fence_stale_results", None)
        if not callable(callback):
            return {"status": "BLOCKED", "reason": "STALE_RESULT_FENCE_UNAVAILABLE"}
        return callback(
            reason=str(getattr(decision, "reason", "STALE_RESULT_REQUIRES_FENCING") or "STALE_RESULT_REQUIRES_FENCING")
        )

    return {
        "RECONCILE_AMBIGUOUS": handler,
        "ASSIGN_WORKER": handler,
        "WAKE_MASTER": handler,
        "RESUME_WORKER": handler,
        "RECOVER_STALLED": handler,
        "REASON_MASTER": handler,
        "BLOCKED": blocked_action,
        "EMERGENCY_STOP": emergency_stop_action,
        "FENCE_STALE_RESULTS": fence_stale_results_action,
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
    execution_mode = (
        "ACTIVE"
        if bool(args.active_controller) or bool(args.supervise_master)
        else "OBSERVE_ONLY"
    )
    stop_request_path = None
    if getattr(args, "stop_request_path", None) is not None:
        stop_request_path = _resolve_stop_request_path(
            pathlib.Path(args.stop_request_path), allowed_root, database_path
        )

    store = StateStore(database_path, [allowed_root])
    scheduler = Scheduler(store, str(args.project_id), PathPolicy([allowed_root]), max_workers=2)
    controller_gateway = None
    lease = None
    graceful_exit = False
    run_loop_started = False
    pipe_listener = None
    pipe_thread = None
    pipe_stop = None
    pipe_errors: list[str] = []
    runtime_pipe_enabled = False
    stop_request: FencedStopRequest | None = None
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
        if stop_request_path is not None:
            stop_request = FencedStopRequest(
                stop_request_path,
                project_id=str(args.project_id),
                daemon_owner=actor_id,
                daemon_epoch=daemon_epoch,
                pid=os.getpid(),
            )

        if args.runtime_pipe_authkey_file is not None:
            authkey = authkey_from_file(args.runtime_pipe_authkey_file)
            pipe_service = RuntimeCommandService(
                store,
                project_id=str(args.project_id),
                daemon_epoch=daemon_epoch,
                actor=str(args.runtime_pipe_actor),
                execution_mode=execution_mode,
            )
            pipe_server = RuntimePipeServer(
                pipe_service,
                project_id=str(args.project_id),
                authkey=authkey,
                actor=str(args.runtime_pipe_actor),
            )
            pipe_listener = create_listener(
                pipe_server.endpoint,
                authkey=authkey,
            )
            pipe_stop = threading.Event()

            def runtime_pipe_loop() -> None:
                while not pipe_stop.is_set():
                    try:
                        pipe_server.serve_once(pipe_listener)
                    except Exception as exc:
                        if pipe_stop.is_set():
                            return
                        pipe_errors.append(
                            type(exc).__name__ + ":" + str(exc)
                        )
                        return

            pipe_thread = threading.Thread(
                target=runtime_pipe_loop,
                name="scorp-runtime-pipe",
                daemon=True,
            )
            pipe_thread.start()
            runtime_pipe_enabled = True

        if (
            rebind_callback is not None
            and callable(getattr(rebind_callback, "verify_current", None))
        ):
            def verify_master_action(decision):
                return rebind_callback.verify_current(
                    daemon_epoch=daemon_epoch,
                    master_epoch=int(decision.master_epoch),
                )

            action_handlers["VERIFY_MASTER"] = verify_master_action

        daemon = LocalDaemon(
            store,
            project_id=str(args.project_id),
            daemon_epoch=daemon_epoch,
            snapshot_provider=lambda: (
                (_ for _ in ()).throw(
                    RuntimeError(
                        "RUNTIME_PIPE_FAILED:" + pipe_errors[0]
                    )
                )
                if pipe_errors
                else dataclasses.replace(
                    store.activation_snapshot(
                        str(args.project_id), daemon_epoch=daemon_epoch
                    ),
                    reasoning_required=bool(
                        reasoning_coordinator is not None
                        and reasoning_coordinator.reasoning_required()
                    ),
                )
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
            paused_master_heartbeat=lambda: (
                controller.heartbeat()
                if controller is not None and controller.master_epoch is not None
                else {"status": "NO_ACTIVE_MASTER"}
            ),
            recovery_callback=lambda: store.record_daemon_recovery(
                str(args.project_id)
            ),
            action_handlers=action_handlers,
            health_path=health_path,
            actor_id=actor_id,
            execution_mode=execution_mode,
            action_heartbeat_interval_seconds=max(
                0.5,
                min(5.0, float(args.daemon_ttl_seconds) / 3.0),
            ),
        )
        run_loop_started = True
        decisions = daemon.run_loop(
            interval_seconds=float(args.interval_seconds),
            max_iterations=None if args.forever else int(args.max_iterations),
            stop_event=stop_request,
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
            "runtime_pipe": "ENABLED" if runtime_pipe_enabled else "DISABLED",
            "execution_mode": execution_mode,
            "scheduling_state": health.get("scheduling_state"),
            "dispatch_allowed": bool(health.get("dispatch_allowed", False)),
            "stop_request": (
                {
                    "accepted": stop_request.request_id is not None,
                    "request_id": stop_request.request_id,
                    "rejection_reason": stop_request.rejection_reason,
                }
                if stop_request is not None
                else None
            ),
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        graceful_exit = True
        return 0 if health["status"] in {"HEALTHY", "TERMINAL"} else 2
    finally:
        if pipe_stop is not None:
            pipe_stop.set()
        if pipe_listener is not None:
            try:
                pipe_listener.close()
            except Exception:
                pass
        if pipe_thread is not None and pipe_thread.is_alive():
            pipe_thread.join(timeout=2.0)
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
        if stop_request is not None and graceful_exit and stop_request.request_id is not None:
            stop_request.acknowledge()


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
