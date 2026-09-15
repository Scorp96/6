"""Fail-closed stdio façade for an approved local Runtime connector.

The façade is intentionally narrower than the Runtime itself: it binds one
configured actor identity, accepts one JSON request per line, and forwards the
request through :class:`RuntimePipeClient`.  It has no shell, filesystem,
browser, Git, or SQLite operation of its own and never retries a transport
timeout.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from .runtime_pipe import MAX_MESSAGE_BYTES, RuntimePipeError
from .runtime_pipe_client import RuntimePipeClient
from .runtime_pipe_cli import authkey_from_env
from .runtime_protocol import RuntimeProtocolError, error_response, parse_request


def _identity(raw: Any) -> tuple[str, str, str]:
    if not isinstance(raw, Mapping):
        return "", "", ""
    return (
        str(raw.get("request_id") or ""),
        str(raw.get("command") or ""),
        str(raw.get("project_id") or ""),
    )


class RuntimeConnectorSession:
    """Bind connector input to one actor and one project scope."""

    def __init__(self, client: RuntimePipeClient, *, actor: str, project_id: str) -> None:
        if not isinstance(client, RuntimePipeClient) and not hasattr(client, "request"):
            raise TypeError("CONNECTOR_CLIENT_INVALID")
        self.client = client
        self.actor = str(actor or "").strip()
        self.project_id = str(project_id or "").strip()
        if not self.actor:
            raise ValueError("CONNECTOR_ACTOR_REQUIRED")
        if not self.project_id:
            raise ValueError("CONNECTOR_PROJECT_REQUIRED")

    def handle_line(self, line: str) -> dict[str, Any]:
        if not isinstance(line, str):
            return error_response("", status="REJECTED", code="CONNECTOR_LINE_REQUIRED")
        if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
            return error_response("", status="REJECTED", code="CONNECTOR_MESSAGE_TOO_LARGE")
        try:
            raw = json.loads(line)
        except (TypeError, ValueError):
            return error_response("", status="REJECTED", code="INVALID_JSON")
        request_id, command, request_project = _identity(raw)
        try:
            request = parse_request(raw)
            if request.project_id != self.project_id:
                return error_response(
                    request.request_id,
                    status="REJECTED",
                    code="PROJECT_SCOPE_MISMATCH",
                    command=request.command,
                    project_id=request.project_id,
                )
            if request.actor != self.actor:
                return error_response(
                    request.request_id,
                    status="REJECTED",
                    code="ACTOR_SCOPE_MISMATCH",
                    command=request.command,
                    project_id=request.project_id,
                )
            return self.client.request(raw)
        except RuntimeProtocolError as exc:
            return error_response(
                request_id,
                status="REJECTED",
                code=exc.code,
                detail=exc.detail,
                command=command,
                project_id=request_project,
            )
        except RuntimePipeError as exc:
            return error_response(
                request_id,
                status="BLOCKED",
                code=str(exc),
                command=command,
                project_id=request_project,
            )
        except (OSError, EOFError) as exc:
            return error_response(
                request_id,
                status="BLOCKED",
                code="CONNECTOR_TRANSPORT_UNAVAILABLE",
                detail=type(exc).__name__,
                command=command,
                project_id=request_project,
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scorp-runtime-connector")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--actor", default="gpt-master")
    parser.add_argument("--authkey-env", default="SCORP_RUNTIME_PIPE_AUTHKEY")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        authkey = authkey_from_env(args.authkey_env)
        session = RuntimeConnectorSession(
            RuntimePipeClient(
                project_id=args.project_id,
                authkey=authkey,
                timeout_seconds=args.timeout_seconds,
            ),
            actor=args.actor,
            project_id=args.project_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"runtime connector startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    for line in sys.stdin:
        if not line.strip():
            continue
        response = session.handle_line(line.strip())
        sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

