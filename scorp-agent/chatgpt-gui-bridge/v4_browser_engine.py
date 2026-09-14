"""Adapter from the existing actor drivers to the V4 browser contract.

The legacy drivers are deliberately kept behind this seam.  A snapshot alone
is never treated as a captured response; callers must inject a parser that can
recognize a structured response belonging to the submitted intent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from gui_engine import ChatGptGuiEngine
from gui_transport import extract_conversation_url


ResponseParser = Callable[[str, str], Mapping[str, Any] | None]


def _remote_identity(intent_id: str, url: str, response: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"intent_id": intent_id, "url": url, "response": dict(response)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_v4_browser_engine(
    driver: Any,
    *,
    auth_probe: Callable[[str], Any] | None,
    response_parser: ResponseParser | None = None,
    timeout_seconds: int = 900,
) -> ChatGptGuiEngine:
    """Build a V4 engine around a Windows MCP or Chrome Use actor driver."""

    async def run_turn(
        prompt: str,
        request_id: str,
        *,
        timeout_seconds: int,
        conversation_url: str | None = None,
        actor_kind: str = "MASTER",
    ):
        result = await asyncio.wait_for(
            driver.submit_prompt(
                prompt=prompt,
                turn_id=request_id,
                actor_kind=actor_kind,
                conversation_url=conversation_url,
            ),
            timeout=float(timeout_seconds),
        )
        snapshot = str(result or "")
        url = extract_conversation_url(snapshot)
        if conversation_url and url and url != conversation_url:
            parsed = None
        else:
            parsed = response_parser(snapshot, request_id) if response_parser else None
        response = dict(parsed) if isinstance(parsed, Mapping) else {}
        return response, snapshot, url

    async def reconcile_probe(intent: Mapping[str, Any]):
        url = str(intent.get("conversation_url") or "").strip()
        if not url:
            return {"status": "AMBIGUOUS", "reason": "CONVERSATION_URL_MISSING"}
        snapshot = await asyncio.wait_for(
            driver.snapshot_conversation(url), timeout=float(timeout_seconds)
        )
        text = str(snapshot or "")
        intent_id = str(intent.get("intent_id") or "")
        observed_url = extract_conversation_url(text)
        if observed_url and observed_url != url:
            return {
                "status": "AMBIGUOUS",
                "reason": "CONVERSATION_URL_MISMATCH",
                "conversation_url": observed_url,
            }
        parsed = response_parser(text, intent_id) if response_parser else None
        if not isinstance(parsed, Mapping) or not parsed:
            return {"status": "AMBIGUOUS", "reason": "STRUCTURED_RESPONSE_NOT_CAPTURED"}
        observed_url = observed_url or url
        return {
            "status": "RESPONSE_CAPTURED",
            "conversation_url": observed_url,
            "remote_identity": _remote_identity(intent_id, observed_url, parsed),
            "response": dict(parsed),
            "snapshot": text,
        }

    return ChatGptGuiEngine(
        run_turn,
        auth_probe=auth_probe,
        reconcile_probe=reconcile_probe,
        timeout_seconds=timeout_seconds,
    )
