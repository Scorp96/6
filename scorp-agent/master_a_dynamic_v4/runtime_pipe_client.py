"""Bounded client for the authenticated local Runtime Named Pipe.

This is a transport adapter only.  It validates the existing versioned
Runtime request before connecting, performs one request/response exchange,
and never retries automatically.  A timeout is deliberately surfaced as an
error because the caller must reconcile durable intent before attempting any
new external action.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from multiprocessing.connection import Client
from typing import Any

from .runtime_pipe import MAX_MESSAGE_BYTES, MIN_AUTHKEY_BYTES, RuntimePipeError, _validate_authkey, pipe_name
from .runtime_protocol import RESPONSE_VERSION, RuntimeProtocolError, parse_request


class RuntimePipeClient:
    """Issue one authenticated, project-scoped Runtime command request."""

    def __init__(
        self,
        *,
        project_id: str,
        authkey: bytes,
        timeout_seconds: float = 30.0,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        self.project_id = str(project_id or "").strip()
        self.endpoint = pipe_name(self.project_id)
        self.authkey = _validate_authkey(authkey)
        try:
            timeout = float(timeout_seconds)
            limit = int(max_message_bytes)
        except (TypeError, ValueError) as exc:
            raise ValueError("PIPE_CLIENT_LIMIT_INVALID") from exc
        if timeout <= 0:
            raise ValueError("PIPE_CLIENT_TIMEOUT_INVALID")
        if limit < 1024 or limit > 1024 * 1024:
            raise ValueError("PIPE_CLIENT_MESSAGE_LIMIT_INVALID")
        self.timeout_seconds = timeout
        self.max_message_bytes = limit

    def request(self, raw_request: Mapping[str, Any]) -> dict[str, Any]:
        """Send exactly one request and return its validated JSON response.

        Connection failures and response timeouts are not retried.  Retrying
        after a transport timeout could duplicate an external side effect when
        the Runtime already committed the request or browser intent.
        """

        if not isinstance(raw_request, Mapping):
            raise ValueError("REQUEST_OBJECT_REQUIRED")
        try:
            request = parse_request(raw_request)
        except RuntimeProtocolError as exc:
            raise ValueError(exc.code) from exc
        if request.project_id != self.project_id:
            raise ValueError("PROJECT_SCOPE_MISMATCH")
        encoded = json.dumps(dict(raw_request), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > self.max_message_bytes:
            raise RuntimePipeError("PIPE_MESSAGE_TOO_LARGE")

        connection = Client(self.endpoint, family="AF_PIPE", authkey=self.authkey)
        try:
            connection.send_bytes(encoded)
            if not connection.poll(self.timeout_seconds):
                raise RuntimePipeError("PIPE_RESPONSE_TIMEOUT")
            try:
                response_bytes = connection.recv_bytes(self.max_message_bytes + 1)
            except (OSError, EOFError) as exc:
                raise RuntimePipeError("PIPE_RESPONSE_READ_FAILED") from exc
        finally:
            connection.close()
        if len(response_bytes) > self.max_message_bytes:
            raise RuntimePipeError("PIPE_RESPONSE_TOO_LARGE")
        try:
            response = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimePipeError("PIPE_RESPONSE_INVALID_JSON") from exc
        if not isinstance(response, dict):
            raise RuntimePipeError("PIPE_RESPONSE_OBJECT_REQUIRED")
        if response.get("protocol_version") != RESPONSE_VERSION:
            raise RuntimePipeError("PIPE_RESPONSE_VERSION_INVALID")
        if response.get("request_id") != request.request_id:
            raise RuntimePipeError("PIPE_RESPONSE_REQUEST_ID_MISMATCH")
        if response.get("project_id") != self.project_id:
            raise RuntimePipeError("PIPE_RESPONSE_PROJECT_SCOPE_MISMATCH")
        if response.get("status") not in {"OK", "BLOCKED", "REJECTED", "ERROR"}:
            raise RuntimePipeError("PIPE_RESPONSE_STATUS_INVALID")
        return response


__all__ = ["MIN_AUTHKEY_BYTES", "RuntimePipeClient"]
