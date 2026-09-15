"""Candidate one-shot or supervised launcher for the authenticated Runtime pipe."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from .runtime_commands import RuntimeCommandService
from .runtime_pipe import RuntimePipeServer, create_listener
from .state_store import StateStore, StoreInvariantError


def authkey_from_env(name: str) -> bytes:
    key_name = str(name or "").strip()
    if not key_name:
        raise ValueError("PIPE_AUTHKEY_ENV_REQUIRED")
    value = os.environ.get(key_name)
    if value is None:
        raise ValueError("PIPE_AUTHKEY_ENV_MISSING")
    encoded = value.encode("utf-8")
    if len(encoded) < 16:
        raise ValueError("PIPE_AUTHKEY_TOO_SHORT")
    return encoded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scorp-runtime-pipe")
    parser.add_argument("--database", "--database-path", dest="database", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--allowed-root", action="append", required=True)
    parser.add_argument("--daemon-epoch", required=True, type=int)
    parser.add_argument("--actor", default="gpt-master")
    parser.add_argument("--authkey-env", default="SCORP_RUNTIME_PIPE_AUTHKEY")
    parser.add_argument("--once", action="store_true", help="serve exactly one authenticated connection")
    parser.add_argument("--max-connections", type=int, default=0, help="optional bounded connection count; 0 means run until stopped")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.daemon_epoch < 0:
        _parser().error("--daemon-epoch must be non-negative")
    if args.max_connections < 0:
        _parser().error("--max-connections must be non-negative")
    try:
        authkey = authkey_from_env(args.authkey_env)
        store = StateStore(args.database, args.allowed_root)
    except (OSError, StoreInvariantError, ValueError) as exc:
        print(f"runtime pipe startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        service = RuntimeCommandService(
            store,
            project_id=args.project_id,
            daemon_epoch=args.daemon_epoch,
            actor=args.actor,
        )
        server = RuntimePipeServer(
            service,
            project_id=args.project_id,
            authkey=authkey,
            actor=args.actor,
        )
        listener = create_listener(server.endpoint, authkey=authkey)
        handled = 0
        try:
            while args.once or args.max_connections == 0 or handled < args.max_connections:
                server.serve_once(listener)
                handled += 1
                if args.once:
                    break
        except KeyboardInterrupt:
            return 0
        finally:
            listener.close()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"runtime pipe stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
