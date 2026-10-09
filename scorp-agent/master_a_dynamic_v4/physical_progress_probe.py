"""Safe read-only observation of the *already bound* ChatGPT conversation.

Never navigate to a supplied URL: doing so could conceal a conflict between
the browser, the driver's session binding and the authoritative project.
The driver must implement `observe_current_binding(channel)`, which reads
the currently bound URL and a snapshot without focus, navigation or sending.

A visible Stop button can provide positive evidence of ongoing generation,
but the *absence* of such a button is never evidence that the turn finished.
No function here reports IDLE_CONFIRMED or grants a browser send capability.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .session_admission import _canonical_conversation_url


_STOP_BUTTON = re.compile(
    r'(?im)^\s*[-*]?\s*(?:button|按钮)\b[^\n]{0,200}'
    r'(?:stop generating|stop responding|停止生成|停止回答|停止响应)\b'
)


@dataclass(frozen=True)
class PhysicalProgress:
    status: str
    reason: str
    session_id: str | None = None
    conversation_url: str | None = None
    snapshot_sha256: str | None = None
    browser_send_authorized: bool = False


async def observe_bound_progress(
    driver: Any,
    *,
    channel: str,
    expected_conversation_url: str,
    timeout_seconds: float = 20.0,
) -> PhysicalProgress:
    """One read-only probe. Errors and idle-looking UI remain fail-closed."""
    def blocked(reason: str) -> PhysicalProgress:
        return PhysicalProgress("BLOCKED", reason)

    expected = _canonical_conversation_url(expected_conversation_url)
    if expected is None:
        return blocked("EXPECTED_CONVERSATION_URL_INVALID")
    if not isinstance(channel, str) or not channel.strip():
        return blocked("CHANNEL_MISSING")
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 60:
        return blocked("TIMEOUT_INVALID")
    probe = getattr(driver, "observe_current_binding", None)
    if not callable(probe):
        return blocked("READ_ONLY_DRIVER_CAPABILITY_MISSING")
    try:
        result = probe(channel)
        if asyncio.iscoroutine(result):
            observed = await asyncio.wait_for(result, timeout=float(timeout_seconds))
        else:
            observed = result
    except (TimeoutError, RuntimeError, ValueError, OSError) as exc:
        return blocked("PHYSICAL_OBSERVATION_FAILED:" + type(exc).__name__)
    if not isinstance(observed, Mapping):
        return blocked("PHYSICAL_OBSERVATION_INVALID")
    driver_url = _canonical_conversation_url(str(observed.get("driver_url") or ""))
    physical_url = _canonical_conversation_url(str(observed.get("physical_url") or ""))
    if not driver_url or driver_url != physical_url or driver_url != expected:
        return blocked("PHYSICAL_BINDING_CONFLICT")
    session = str(observed.get("session") or "").strip()
    if not session:
        return blocked("PHYSICAL_SESSION_MISSING")
    snapshot = observed.get("snapshot")
    if not isinstance(snapshot, str) or not snapshot or expected not in snapshot:
        return blocked("SNAPSHOT_URL_NOT_CONFIRMED")
    sha = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    if _STOP_BUTTON.search(snapshot):
        return PhysicalProgress("GENERATING", "VISIBLE_STOP_CONTROL", session, expected, sha)
    return PhysicalProgress(
        "UNKNOWN", "NO_AFFIRMATIVE_TERMINAL_PROOF", session, expected, sha,
    )
