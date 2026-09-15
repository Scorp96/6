"""Machine validation for the bounded local Runtime connector contract.

The descriptor is deliberately a contract, not a host registration.  A web
GPT cannot gain local permissions by reading this file.  An approved host must
still register and supervise the connector separately, then provide fresh
evidence for that registration.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .runtime_protocol import ALL_COMMANDS, REQUEST_VERSION
from .runtime_pipe import MAX_MESSAGE_BYTES, MIN_AUTHKEY_BYTES


DESCRIPTOR_FORMAT = "scorp.runtime.connector/1"
_REGISTRATION_STATUSES = frozenset({"UNREGISTERED_HOST", "APPROVED_HOST"})
_FORBIDDEN_SECURITY_FLAGS = frozenset(
    {
        "arbitrary_shell",
        "arbitrary_browser",
        "arbitrary_filesystem",
        "arbitrary_git",
        "arbitrary_network",
    }
)


class ConnectorDescriptorError(ValueError):
    """Raised when a connector descriptor would widen the local capability boundary."""


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConnectorDescriptorError(code)
    return value


def _required_text(mapping: Mapping[str, Any], key: str, code: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConnectorDescriptorError(code)
    return value.strip()


def _required_bool(mapping: Mapping[str, Any], key: str, code: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ConnectorDescriptorError(code)
    return value


def validate_connector_descriptor(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-compatible bounded connector descriptor."""

    descriptor = _mapping(raw, "DESCRIPTOR_OBJECT_REQUIRED")
    if descriptor.get("format") != DESCRIPTOR_FORMAT:
        raise ConnectorDescriptorError("DESCRIPTOR_FORMAT_INVALID")
    if descriptor.get("protocol") != REQUEST_VERSION:
        raise ConnectorDescriptorError("PROTOCOL_VERSION_INVALID")

    registration = _mapping(descriptor.get("registration"), "REGISTRATION_REQUIRED")
    registration_status = _required_text(registration, "status", "REGISTRATION_STATUS_REQUIRED")
    if registration_status not in _REGISTRATION_STATUSES:
        raise ConnectorDescriptorError("REGISTRATION_STATUS_INVALID")
    if not _required_bool(
        registration,
        "requires_explicit_host_registration",
        "HOST_REGISTRATION_FLAG_REQUIRED",
    ):
        raise ConnectorDescriptorError("HOST_REGISTRATION_MUST_BE_EXPLICIT")

    commands = descriptor.get("commands")
    if isinstance(commands, (str, bytes, bytearray)) or not isinstance(commands, Sequence):
        raise ConnectorDescriptorError("COMMANDS_REQUIRED")
    command_values = [value for value in commands if isinstance(value, str) and value.strip()]
    if len(command_values) != len(commands) or len(set(command_values)) != len(command_values):
        raise ConnectorDescriptorError("COMMANDS_INVALID")
    unknown = set(command_values) - set(ALL_COMMANDS)
    if unknown:
        raise ConnectorDescriptorError("COMMAND_NOT_ALLOWED")
    if set(command_values) != set(ALL_COMMANDS):
        raise ConnectorDescriptorError("COMMAND_SET_INCOMPLETE")

    security = _mapping(descriptor.get("security"), "SECURITY_REQUIRED")
    if not _required_bool(security, "actor_binding_required", "ACTOR_SCOPE_REQUIRED"):
        raise ConnectorDescriptorError("ACTOR_SCOPE_REQUIRED")
    if not _required_bool(security, "project_scope_required", "PROJECT_SCOPE_REQUIRED"):
        raise ConnectorDescriptorError("PROJECT_SCOPE_REQUIRED")
    if _required_bool(security, "retry_on_timeout", "RETRY_POLICY_REQUIRED"):
        raise ConnectorDescriptorError("RETRY_POLICY_INVALID")
    for key in _FORBIDDEN_SECURITY_FLAGS:
        if _required_bool(security, key, f"SECURITY_FLAG_REQUIRED:{key}"):
            raise ConnectorDescriptorError("ARBITRARY_CAPABILITY_FORBIDDEN")

    transport = _mapping(descriptor.get("transport"), "TRANSPORT_REQUIRED")
    try:
        max_message_bytes = int(transport.get("max_message_bytes"))
    except (TypeError, ValueError) as exc:
        raise ConnectorDescriptorError("MESSAGE_LIMIT_INVALID") from exc
    if max_message_bytes < 1024 or max_message_bytes > MAX_MESSAGE_BYTES:
        raise ConnectorDescriptorError("MESSAGE_LIMIT_INVALID")
    authkey_env = _required_text(transport, "authkey_env", "AUTHKEY_ENV_REQUIRED")
    if len(authkey_env) > 128 or not authkey_env.startswith("SCORP_"):
        raise ConnectorDescriptorError("AUTHKEY_ENV_INVALID")
    try:
        min_authkey_bytes = int(transport.get("min_authkey_bytes"))
    except (TypeError, ValueError) as exc:
        raise ConnectorDescriptorError("AUTHKEY_LENGTH_INVALID") from exc
    if min_authkey_bytes < MIN_AUTHKEY_BYTES:
        raise ConnectorDescriptorError("AUTHKEY_LENGTH_INVALID")

    entrypoints = _mapping(descriptor.get("entrypoints"), "ENTRYPOINTS_REQUIRED")
    for key in ("stdio", "windows_named_pipe"):
        entrypoint = _mapping(entrypoints.get(key), f"ENTRYPOINT_REQUIRED:{key}")
        _required_text(entrypoint, "module", f"ENTRYPOINT_MODULE_REQUIRED:{key}")

    return json.loads(json.dumps(dict(descriptor), ensure_ascii=False))


def load_connector_descriptor(path: str | Path) -> dict[str, Any]:
    """Load and validate one descriptor without starting a connector."""

    descriptor_path = Path(path)
    try:
        raw = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConnectorDescriptorError("DESCRIPTOR_READ_FAILED") from exc
    return validate_connector_descriptor(raw)


__all__ = [
    "DESCRIPTOR_FORMAT",
    "ConnectorDescriptorError",
    "load_connector_descriptor",
    "validate_connector_descriptor",
]
