"""Fail-closed classification for the existing ChatGPT browser session."""

from __future__ import annotations

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
