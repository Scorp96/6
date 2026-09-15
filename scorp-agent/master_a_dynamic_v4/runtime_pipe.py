"""Authenticated, bounded Windows Named Pipe transport for Runtime commands.

The transport is deliberately a thin adapter around the existing versioned
JSON protocol and ``RuntimeCommandService``.  It adds no command capability,
does not interpret shell-like text, and never exposes a network listener.  A
caller must provide a local ``multiprocessing.connection`` auth key; the key
is not accepted in a request message and is never included in responses.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from multiprocessing.connection import Listener
from typing import Any

from .runtime_commands import RuntimeCommandService
from .runtime_protocol import RuntimeProtocolError, error_response, parse_request


MAX_MESSAGE_BYTES = 64 * 1024
MIN_AUTHKEY_BYTES = 16
_PROJECT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class RuntimePipeError(RuntimeError):
    """Raised when the local Named Pipe transport cannot be used safely."""


def pipe_name(project_id: str) -> str:
    """Return the fixed Windows pipe path for one project scope."""

    value = str(project_id or "").strip()
    if _PROJECT_ID.fullmatch(value) is None:
        raise ValueError("PIPE_PROJECT_ID_INVALID")
    return rf"\\.\pipe\scorp-runtime-{value}"


def _validate_authkey(authkey: bytes) -> bytes:
    if not isinstance(authkey, (bytes, bytearray)):
        raise ValueError("PIPE_AUTHKEY_REQUIRED")
    value = bytes(authkey)
    if len(value) < MIN_AUTHKEY_BYTES:
        raise ValueError("PIPE_AUTHKEY_TOO_SHORT")
    return value


def create_listener(endpoint: str, *, authkey: bytes) -> Listener:
    """Create an authenticated Windows Named Pipe listener.

    The explicit platform check prevents silently falling back to a TCP or
    filesystem transport on another operating system.
    """

    if os.name != "nt":
        raise RuntimePipeError("WINDOWS_NAMED_PIPE_UNAVAILABLE")
    value = str(endpoint or "").strip()
    if not value.startswith(r"\\.\pipe\scorp-runtime-"):
        raise ValueError("PIPE_ENDPOINT_INVALID")
    return Listener(value, family="AF_PIPE", authkey=_validate_authkey(authkey), backlog=1)


def _request_identity(raw: Any) -> tuple[str, str, str]:
    if not isinstance(raw, Mapping):
        return "", "", ""
    return (
        str(raw.get("request_id") or ""),
        str(raw.get("command") or ""),
        str(raw.get("project_id") or ""),
    )


class RuntimePipeServer:
    """Handle one or more authenticated local Runtime command connections."""

    def __init__(
        self,
        service: RuntimeCommandService,
        *,
        project_id: str,
        authkey: bytes,
        endpoint: str | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ):
        if not isinstance(service, RuntimeCommandService):
            raise TypeError("PIPE_SERVICE_INVALID")
        self.service = service
        self.project_id = str(project_id or "").strip()
        if _PROJECT_ID.fullmatch(self.project_id) is None:
            raise ValueError("PIPE_PROJECT_ID_INVALID")
        self.authkey = _validate_authkey(authkey)
        try:
            limit = int(max_message_bytes)
        except (TypeError, ValueError) as exc:
            raise ValueError("PIPE_MESSAGE_LIMIT_INVALID") from exc
        if limit < 1024 or limit > 1024 * 1024:
            raise ValueError("PIPE_MESSAGE_LIMIT_INVALID")
        self.max_message_bytes = limit
        self.endpoint = str(endpoint or pipe_name(self.project_id))
        if self.endpoint != pipe_name(self.project_id):
            raise ValueError("PIPE_ENDPOINT_SCOPE_MISMATCH")

    def handle_bytes(self, raw_bytes: bytes) -> bytes:
        """Parse and dispatch one bounded request, returning one JSON response."""

        if not isinstance(raw_bytes, (bytes, bytearray)):
            response = error_response("", status="REJECTED", code="PIPE_MESSAGE_BYTES_REQUIRED")
            return self._encode_response(response)
        if len(raw_bytes) > self.max_message_bytes:
            response = error_response("", status="REJECTED", code="PIPE_MESSAGE_TOO_LARGE")
            return self._encode_response(response)
        request_id = command = request_project = ""
        try:
            raw = json.loads(bytes(raw_bytes).decode("utf-8"))
            request_id, command, request_project = _request_identity(raw)
            request = parse_request(raw)
            if request.project_id != self.project_id:
                return self._encode_response(
                    error_response(
                        request.request_id,
                        status="REJECTED",
                        code="PROJECT_SCOPE_MISMATCH",
                        command=request.command,
                        project_id=request.project_id,
                    )
                )
            return self._encode_response(self.service.execute(request))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = error_response(
                request_id, status="REJECTED", code="INVALID_JSON", command=command, project_id=request_project
            )
        except RuntimeProtocolError as exc:
            response = error_response(
                request_id,
                status="REJECTED",
                code=exc.code,
                detail=exc.detail,
                command=command,
                project_id=request_project,
            )
        except Exception as exc:
            response = error_response(
                request_id,
                status="ERROR",
                code="PIPE_HANDLER_ERROR",
                detail=type(exc).__name__,
                command=command,
                project_id=request_project,
            )
        return self._encode_response(response)

    def _encode_response(self, response: Mapping[str, Any]) -> bytes:
        encoded = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= self.max_message_bytes:
            return encoded
        fallback = error_response(
            str(response.get("request_id") or ""),
            status="ERROR",
            code="PIPE_RESPONSE_TOO_LARGE",
            command=str(response.get("command") or ""),
            project_id=str(response.get("project_id") or ""),
        )
        return json.dumps(fallback, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def serve_once(self, listener: Any | None = None) -> None:
        """Accept and handle one authenticated connection.

        Passing an already-created listener is useful for a supervisor that
        owns the listener lifetime and for deterministic tests.  When omitted,
        this method creates and closes one listener itself.
        """

        owned = listener is None
        current = listener or create_listener(self.endpoint, authkey=self.authkey)
        try:
            connection = current.accept()
            try:
                raw = connection.recv_bytes(self.max_message_bytes + 1)
                connection.send_bytes(self.handle_bytes(raw))
            finally:
                connection.close()
        finally:
            if owned:
                current.close()

