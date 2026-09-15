"""Run the V4 local daemon against an existing SQLite state database.

This entrypoint is intentionally monitor-only in its first production seam:
it reads the durable snapshot, journals the ActivationArbiter decision and
reports blockers.  Browser sends, Master rebinds and Worker claims remain
owned by their fenced adapters and are never guessed here.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from master_a_dynamic_v4.daemon import LocalDaemon, MasterSupervisorActionHandler  # noqa: E402
from master_a_dynamic_v4.master_controller import MasterAController  # noqa: E402
from master_a_dynamic_v4.master_supervisor import MasterSupervisor  # noqa: E402
from master_a_dynamic_v4.state_store import StateStore  # noqa: E402
from v4_bridge_gateway import V4BridgeGateway  # noqa: E402


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
    parser.add_argument("--actor-id", default="scorp-daemon")
    parser.add_argument("--daemon-ttl-seconds", type=int, default=30)
    parser.add_argument("--health-path", type=pathlib.Path)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--forever", action="store_true")
    parser.add_argument("--supervise-master", action="store_true")
    parser.add_argument("--master-session-id", default="master-a-runtime")
    return parser


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
    health_path = pathlib.Path(args.health_path).resolve() if args.health_path else database_path.with_suffix(".daemon-health.json")

    store = StateStore(database_path, [allowed_root])
    supervisor_gateway = None
    lease = None
    graceful_exit = False
    try:
        recovery_gate = store.daemon_recovery_gate(str(args.project_id))
        if recovery_gate["status"] != "ALLOWED":
            raise RuntimeError(
                f"DAEMON_RECOVERY_{recovery_gate['status']}:{recovery_gate.get('reason', '')}"
            )
        lease = store.acquire_daemon_lease(
            str(args.project_id),
            str(args.actor_id),
            ttl_seconds=int(args.daemon_ttl_seconds),
        )
        if args.daemon_epoch is not None and int(lease["daemon_epoch"]) != int(args.daemon_epoch):
            raise RuntimeError(
                f"DAEMON_EPOCH_MISMATCH expected={args.daemon_epoch} actual={lease['daemon_epoch']}"
            )
        daemon_epoch = int(lease["daemon_epoch"])
        action_handlers = {}
        master_supervision = False
        if args.supervise_master:
            supervisor_gateway = V4BridgeGateway(
                database_path,
                str(args.project_id),
                [allowed_root],
                MonitorOnlyEngine(),
                master_ttl_seconds=max(60, int(args.daemon_ttl_seconds)),
            )
            controller = MasterAController(supervisor_gateway, str(args.master_session_id))
            controller.attach_existing_session()
            supervisor = MasterSupervisor(controller)
            supervisor_handler = MasterSupervisorActionHandler(supervisor)
            action_handlers = {
                "RESUME_MASTER": supervisor_handler,
                "HEARTBEAT_IDLE": supervisor_handler,
            }
            master_supervision = True
        daemon = LocalDaemon(
            store,
            project_id=str(args.project_id),
            daemon_epoch=daemon_epoch,
            snapshot_provider=lambda: store.activation_snapshot(
                str(args.project_id), daemon_epoch=daemon_epoch
            ),
            lease_heartbeat=lambda: store.heartbeat_daemon_lease(
                str(args.project_id),
                str(args.actor_id),
                daemon_epoch=daemon_epoch,
                ttl_seconds=int(args.daemon_ttl_seconds),
            ),
            action_handlers=action_handlers,
            health_path=health_path,
            actor_id=str(args.actor_id),
        )
        decisions = daemon.run_loop(
            interval_seconds=float(args.interval_seconds),
            max_iterations=None if args.forever else int(args.max_iterations),
        )
        health = json.loads(health_path.read_text(encoding="utf-8"))
        if health["status"] in {"HEALTHY", "TERMINAL"}:
            store.record_daemon_recovery(str(args.project_id))
        summary = {
            "format": "scorp-v4-daemon-run/1",
            "status": health["status"],
            "project_id": str(args.project_id),
            "daemon_epoch": daemon_epoch,
            "decision_count": len(decisions),
            "decisions": [decision.as_dict() for decision in decisions],
            "health_path": str(health_path),
            "browser_send": "FORBIDDEN",
            "master_supervision": master_supervision,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        graceful_exit = True
        return 0 if health["status"] in {"HEALTHY", "TERMINAL"} else 2
    finally:
        if graceful_exit and lease is not None:
            store.release_daemon_lease(
                str(args.project_id),
                str(args.actor_id),
                daemon_epoch=int(lease["daemon_epoch"]),
            )
        if supervisor_gateway is not None:
            supervisor_gateway.close()
        store.close()


def main(argv: list[str] | None = None) -> int:
    return run_runtime(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
