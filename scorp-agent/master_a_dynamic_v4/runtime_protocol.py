"""Versioned, fail-closed protocol for the local runtime command boundary.

This module deliberately contains no transport or side effects.  It validates a
small JSON-compatible envelope before a command service is allowed to touch the
transaction store.  Keeping this boundary pure makes it usable from stdin,
future named pipes, and tests without granting those transports shell access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import sha256_json


REQUEST_VERSION = "scorp.runtime.command/1"
RESPONSE_VERSION = "scorp.runtime.response/1"

READ_COMMANDS = frozenset(
    {"runtime.status", "project.status", "master.status", "evidence.query"}
)
MUTATION_COMMANDS = frozenset(
    {
        "project.pause",
        "project.resume",
        "project.cancel",
        "project.supersede",
    }
)
ALL_COMMANDS = READ_COMMANDS | MUTATION_COMMANDS
MUTATION_FIELDS = frozenset(
    {"expected_state_version", "expected_daemon_epoch"}
)
REQUEST_FIELDS = frozenset(
    {
        "protocol_version",
        "request_id",
        "command",
        "project_id",
        "expected_state_version",
        "expected_daemon_epoch",
        "payload",
    }
)


class RuntimeProtocolError(ValueError):
    """A deterministic validation error safe to return to a local caller."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = code if not detail else f"{code}: {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class RuntimeRequest:
    protocol_version: str
    request_id: str
    command: str
    project_id: str
    payload: dict[str, Any]
    expected_state_version: int | None = None
    expected_daemon_epoch: int | None = None

    @property
    def payload_sha256(self) -> str:
        return sha256_json(self.payload)

    @property
    def is_mutation(self) -> bool:
        return self.command in MUTATION_COMMANDS


def _require_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeProtocolError(code)
    return value.strip()


def _optional_nonnegative_int(value: Any, code: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeProtocolError(code)
    return value


def parse_request(raw: Mapping[str, Any]) -> RuntimeRequest:
    """Validate and normalize one request without performing any I/O."""

    if not isinstance(raw, Mapping):
        raise RuntimeProtocolError("REQUEST_OBJECT_REQUIRED")
    unknown = sorted(set(raw) - REQUEST_FIELDS)
    if unknown:
        raise RuntimeProtocolError("UNKNOWN_REQUEST_FIELD", ",".join(unknown))
    if raw.get("protocol_version") != REQUEST_VERSION:
        raise RuntimeProtocolError("UNSUPPORTED_PROTOCOL_VERSION")
    request_id = _require_text(raw.get("request_id"), "REQUEST_ID_REQUIRED")
    command = _require_text(raw.get("command"), "COMMAND_REQUIRED")
    if command not in ALL_COMMANDS:
        raise RuntimeProtocolError("UNKNOWN_COMMAND", command)
    project_id = _require_text(raw.get("project_id"), "PROJECT_ID_REQUIRED")
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeProtocolError("PAYLOAD_OBJECT_REQUIRED")
    expected_state_version = _optional_nonnegative_int(
        raw.get("expected_state_version"), "INVALID_EXPECTED_STATE_VERSION"
    )
    expected_daemon_epoch = _optional_nonnegative_int(
        raw.get("expected_daemon_epoch"), "INVALID_EXPECTED_DAEMON_EPOCH"
    )
    if command in MUTATION_COMMANDS:
        if expected_state_version is None:
            raise RuntimeProtocolError("EXPECTED_STATE_VERSION_REQUIRED")
        if expected_daemon_epoch is None:
            raise RuntimeProtocolError("EXPECTED_DAEMON_EPOCH_REQUIRED")
    return RuntimeRequest(
        protocol_version=REQUEST_VERSION,
        request_id=request_id,
        command=command,
        project_id=project_id,
        payload=dict(payload),
        expected_state_version=expected_state_version,
        expected_daemon_epoch=expected_daemon_epoch,
    )


def build_response(
    request: RuntimeRequest,
    *,
    status: str,
    daemon_epoch: int | None = None,
    state_version: int | None = None,
    result: Mapping[str, Any] | None = None,
    receipt_id: str | None = None,
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable response envelope; callers provide no arbitrary fields."""

    if status not in {"OK", "BLOCKED", "REJECTED", "ERROR"}:
        raise ValueError("invalid runtime response status")
    return {
        "protocol_version": RESPONSE_VERSION,
        "request_id": request.request_id,
        "status": status,
        "command": request.command,
        "project_id": request.project_id,
        "daemon_epoch": daemon_epoch,
        "state_version": state_version,
        "result": dict(result or {}),
        "receipt_id": receipt_id,
        "error": dict(error) if error is not None else None,
    }


def error_response(
    request_id: str,
    *,
    status: str,
    code: str,
    detail: str = "",
    command: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """Return a response for malformed input when no RuntimeRequest exists."""

    return {
        "protocol_version": RESPONSE_VERSION,
        "request_id": request_id,
        "status": status,
        "command": command,
        "project_id": project_id,
        "daemon_epoch": None,
        "state_version": None,
        "result": {},
        "receipt_id": None,
        "error": {"code": code, "detail": detail},
    }


def protocol_error(
    request_id: str | None = None,
    *,
    code: str,
    detail: str = "",
    status: str = "REJECTED",
    command: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """Planned public name for malformed-envelope responses.

    ``error_response`` remains the implementation name for compatibility with
    the CLI; both functions produce the identical versioned envelope.
    """
    return error_response(
        str(request_id or ""),
        status=status,
        code=code,
        detail=detail,
        command=command,
        project_id=project_id,
    )
