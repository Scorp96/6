"""Fail-closed classification for the existing ChatGPT browser session."""

from __future__ import annotations

import json
from typing import Any

from gui_transport import chatgpt_throttle_visible


def classify_chatgpt_snapshot(snapshot: str, channel: str) -> dict[str, Any]:
    """Classify a read-only ChatGPT page before any submit intent is sent.

    A rate-limit dialog takes precedence over positive login markers such as
    ``Plus``.  This prevents a limited page from being mistaken for a usable
    authenticated session and reaching the durable ``MAY_HAVE_SUBMITTED``
    boundary.
    """

    text = str(snapshot or "")
    lowered = text.casefold()
    if chatgpt_throttle_visible(text):
        return {
            "status": "BROWSER_RATE_LIMITED",
            "channel": channel,
            "reason": "CHATGPT_RATE_LIMIT_DIALOG",
        }
    if any(token in lowered for token in ("登录", "log in", "sign up", "登录 chatgpt")):
        return {"status": "AUTHENTICATION_REQUIRED", "channel": channel}
    if any(token in lowered for token in ("captcha", "验证码")):
        return {"status": "CAPTCHA_REQUIRED", "channel": channel}
    if "plus" in lowered or "准备好了" in text or "ready" in lowered:
        return {"status": "AUTHENTICATED", "channel": channel}
    return {"status": "AUTH_PROBE_UNCERTAIN", "channel": channel}


async def probe_chatgpt_auth(
    cli,
    driver,
    session: str,
    channel: str,
    *,
    classify_fn=classify_chatgpt_snapshot,
    recovery_wait_seconds: float = 300,
    recovery_poll_seconds: float = 5,
) -> dict[str, Any]:
    """Read auth state and explicitly recover one visible rate-limit dialog.

    Recovery is limited to the known acknowledgement action and a bounded
    read-only wait.  The caller still has to perform the later submit intent;
    this helper never types or sends a prompt.
    """

    async def read_and_classify() -> dict[str, Any]:
        page = await cli.run_json(session, "read", timeout_seconds=30)
        text = json.dumps(page, ensure_ascii=False)
        return classify_fn(text, channel)

    result = await read_and_classify()
    if result.get("status") != "BROWSER_RATE_LIMITED":
        return result
    recovery = await driver.recover_rate_limit_dialog(
        session,
        max_wait_seconds=recovery_wait_seconds,
        poll_seconds=recovery_poll_seconds,
    )
    if recovery.get("status") != "RECOVERED":
        blocked = dict(result)
        blocked["rate_limit_recovery"] = recovery
        return blocked
    final = await read_and_classify()
    final["rate_limit_recovery"] = recovery
    return final
