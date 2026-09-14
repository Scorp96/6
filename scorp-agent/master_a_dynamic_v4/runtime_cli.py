"""One-request-per-line local runtime command CLI.

The CLI is intentionally boring: it parses JSON, delegates to the bounded
command service, and prints JSON. It does not import a shell, evaluate code,
spawn a process, navigate a browser, or accept a path from the request body.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from .runtime_commands import RuntimeCommandService
from .runtime_protocol import RuntimeProtocolError, error_response, parse_request
from .state_store import StateStore, StoreInvariantError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scorp-runtime")
    parser.add_argument("--database", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--allowed-root", action="append", required=True)
    parser.add_argument("--daemon-epoch", required=True, type=int)
    parser.add_argument("--actor", default="runtime-cli")
    return parser


def _request_identity(raw: Any) -> tuple[str, str, str]:
    if not isinstance(raw, Mapping):
        return "", "", ""
    return (
        str(raw.get("request_id") or ""),
        str(raw.get("command") or ""),
        str(raw.get("project_id") or ""),
    )


def _handle_line(service: RuntimeCommandService, line: str, project_id: str) -> dict[str, Any]:
    try:
        raw = json.loads(line)
    except (TypeError, ValueError):
        return error_response("", status="REJECTED", code="INVALID_JSON")
    request_id, command, request_project = _request_identity(raw)
    try:
        request = parse_request(raw)
        if request.project_id != project_id:
            return error_response(
                request.request_id,
                status="REJECTED",
                code="PROJECT_SCOPE_MISMATCH",
                command=request.command,
                project_id=request.project_id,
            )
        return service.execute(request)
    except RuntimeProtocolError as exc:
        return error_response(
            request_id,
            status="REJECTED",
            code=exc.code,
            detail=exc.detail,
            command=command,
            project_id=request_project,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.daemon_epoch < 0:
        _parser().error("--daemon-epoch must be non-negative")
    try:
        store = StateStore(args.database, args.allowed_root)
    except (OSError, StoreInvariantError, ValueError) as exc:
        print(f"runtime startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    try:
        service = RuntimeCommandService(store, daemon_epoch=args.daemon_epoch, actor=args.actor)
        for line in sys.stdin:
            if not line.strip():
                continue
            response = _handle_line(service, line.strip(), args.project_id)
            sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
