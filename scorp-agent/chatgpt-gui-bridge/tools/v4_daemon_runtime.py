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

from master_a_dynamic_v4.daemon import LocalDaemon  # noqa: E402
from master_a_dynamic_v4.state_store import StateStore  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SCORP V4 local daemon")
    parser.add_argument("--database-path", required=True, type=pathlib.Path)
    parser.add_argument("--allowed-root", required=True, type=pathlib.Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--daemon-epoch", required=True, type=int)
    parser.add_argument("--actor-id", default="scorp-daemon")
    parser.add_argument("--health-path", type=pathlib.Path)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--forever", action="store_true")
    return parser


def run_runtime(args: argparse.Namespace) -> int:
    database_path = pathlib.Path(args.database_path).resolve()
    if not database_path.is_file():
        raise RuntimeError("STATE_DATABASE_MISSING")
    allowed_root = pathlib.Path(args.allowed_root).resolve(strict=True)
    if int(args.daemon_epoch) < 0:
        raise RuntimeError("DAEMON_EPOCH_INVALID")
    if float(args.interval_seconds) < 0:
        raise RuntimeError("DAEMON_INTERVAL_INVALID")
    if not args.forever and int(args.max_iterations) <= 0:
        raise RuntimeError("DAEMON_ITERATION_BOUND_INVALID")
    health_path = pathlib.Path(args.health_path).resolve() if args.health_path else database_path.with_suffix(".daemon-health.json")

    store = StateStore(database_path, [allowed_root])
    try:
        daemon = LocalDaemon(
            store,
            project_id=str(args.project_id),
            daemon_epoch=int(args.daemon_epoch),
            snapshot_provider=lambda: store.activation_snapshot(
                str(args.project_id), daemon_epoch=int(args.daemon_epoch)
            ),
            health_path=health_path,
            actor_id=str(args.actor_id),
        )
        decisions = daemon.run_loop(
            interval_seconds=float(args.interval_seconds),
            max_iterations=None if args.forever else int(args.max_iterations),
        )
        health = json.loads(health_path.read_text(encoding="utf-8"))
        summary = {
            "format": "scorp-v4-daemon-run/1",
            "status": health["status"],
            "project_id": str(args.project_id),
            "daemon_epoch": int(args.daemon_epoch),
            "decision_count": len(decisions),
            "decisions": [decision.as_dict() for decision in decisions],
            "health_path": str(health_path),
            "browser_send": "FORBIDDEN",
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if health["status"] in {"HEALTHY", "TERMINAL"} else 2
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    return run_runtime(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
