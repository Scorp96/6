"""Bounded loopback-only, zero-model-call Chrome Use observation adapter.

The first-party Chrome Use daemon exposes read-only HTTP GET
/api/v1/status, /api/v1/tabs and /api/v1/sessions on 127.0.0.1.
An external helper obtains the port from the pinned session's native
stream-status CLI; never accept an arbitrary host or caller-provided URL.

These endpoints report available daemon and tab data, NOT a verified
ChatGPT assistant-final event, logged-in identity, ownership attestation,
or permission to drive a browser.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any
import urllib.error
import urllib.request

from isolated_chrome_namespace_admission_v4 import _CANARY

_MAX_BODY = 262_144


@dataclass(frozen=True)
class LocalChromeReadObservation:
    status: str
    reason: str
    session_count: int | None = None
    tab_count: int | None = None
    status_endpoint_readable: bool = False
    all_three_endpoints_readable: bool = False
    local_model_calls: int = 0
    native_final_event_verified: bool = False
    authentication_verified: bool = False
    browser_send_authorized: bool = False
    local_execution_authorized: bool = False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _bounded_native_status_runner(
    argv, *, capture_output=True, text=True,
    encoding="utf-8", errors="replace", timeout=8,
):
    """Do not use PIPE: a descendant keeping stdout open can hang communicate.

    Bound ONLY the direct chrome-use process wait; terminal output is kept in
    temporary files, not inherited pipes. Return a private, sanitized result.
    """
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": None,
        "stderr": None,
    }
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startup
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    with tempfile.TemporaryFile(mode="w+b") as output, tempfile.TemporaryFile(mode="w+b") as error:
        options["stdout"] = output
        options["stderr"] = error
        proc = subprocess.Popen(argv, **options)
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return SimpleNamespace(returncode=124, stdout="")
        if exit_code != 0:
            return SimpleNamespace(returncode=int(exit_code), stdout="")
        output.seek(0)
        data = output.read(16_385)
        if len(data) > 16_384:
            return SimpleNamespace(returncode=70, stdout="")
        return SimpleNamespace(returncode=0, stdout=data.decode("utf-8",errors="replace"))


def _native_stream_port(
    executable: str, namespace: str, *, runner=None,
) -> int | None:
    if not isinstance(executable, str) or not executable:
        return None
    if not isinstance(namespace, str) or _CANARY.fullmatch(namespace) is None:
        return None
    argv = [
        executable, "--session", namespace,
        "--json", "stream", "status",
    ]
    try:
        process = (runner or _bounded_native_status_runner)(
            argv, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=8,
        )
        if process.returncode != 0:
            return None
        raw = process.stdout
        if not isinstance(raw, str) or len(raw) > 16_384:
            return None
        payload = json.loads(raw)
        data = payload.get("data") if isinstance(payload, dict) else None
        port = data.get("port") if isinstance(data, dict) else None
        if (
            payload.get("success") is True
            and type(port) is int and 1024 <= port <= 65_535
        ):
            return port
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass
    return None


def _read_local_json(port: int, resource: str) -> Any | None:
    if type(port) is not int or not 1024 <= port <= 65_535:
        return None
    if resource not in (
        "/api/v1/status", "/api/v1/tabs", "/api/v1/sessions",
    ):
        return None
    # Avoid environment-provided proxy settings redirecting loopback
    # requests to external services. No POST, cookies or Authorization.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect(),
    )
    url = "http://127.0.0.1:" + str(port) + resource
    req = urllib.request.Request(
        url, method="GET", headers={"Accept": "application/json"},
    )
    try:
        with opener.open(req, timeout=3) as res:
            if res.status != 200:
                return None
            data = res.read(_MAX_BODY + 1)
        if len(data) > _MAX_BODY:
            return None
        value = json.loads(data)
        return value if isinstance(value, (dict, list)) else None
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        return None


def _items(payload: Any, key: str) -> list | None:
    if isinstance(payload, list):
        return payload if len(payload) <= 256 else None
    if not isinstance(payload, dict):
        return None
    rows = payload.get(key)
    if rows is None and isinstance(payload.get("data"), dict):
        rows = payload["data"].get(key)
    return rows if isinstance(rows, list) and len(rows) <= 256 else None


def observe_local_chrome_status_no_send(
    *,
    executable: str,
    pinned_isolated_namespace: str,
    runner=None,
    reader=None,
) -> LocalChromeReadObservation:
    """Read only known loopback endpoints; never include raw source fields."""
    def result(status, reason, sessions=None, tabs=None, status_ok=False, all_ok=False):
        return LocalChromeReadObservation(
            status, reason, sessions, tabs, status_ok, all_ok,
        )
    if (
        not isinstance(pinned_isolated_namespace, str)
        or _CANARY.fullmatch(pinned_isolated_namespace) is None
    ):
        return result("BLOCKED", "PINNED_ISOLATED_SESSION_REQUIRED")
    port = _native_stream_port(
        executable, pinned_isolated_namespace, runner=runner,
    )
    if port is None:
        return result("BLOCKED", "VALID_NATIVE_STREAM_PORT_NOT_VERIFIED")

    read = reader or _read_local_json
    # Endpoints intentionally limited to three literal fixed names.
    try:
        status = read(port, "/api/v1/status")
        tabs = read(port, "/api/v1/tabs")
        sessions = read(port, "/api/v1/sessions")
    except Exception:
        return result("BLOCKED", "LOOPBACK_READ_FAILED")
    status_ok = isinstance(status, (dict, list))
    tab_items = _items(tabs, "tabs")
    session_items = _items(sessions, "sessions")
    if not status_ok or tab_items is None or session_items is None:
        return result(
            "BLOCKED", "LOOPBACK_STATUS_OR_INVENTORY_UNVERIFIED",
            status_ok=status_ok,
        )
    # Endpoint tab lists are *last observed* snapshots and may be stale.
    # None of them proves a real assistant final turn or live login state.
    return result(
        "LOCAL_STATE_OBSERVED_UNATTESTED",
        "LAST_OBSERVED_BROWSER_COUNTS_ONLY_NO_GPT_TERMINAL_PROOF",
        sessions=len(session_items), tabs=len(tab_items),
        status_ok=True, all_ok=True,
    )
