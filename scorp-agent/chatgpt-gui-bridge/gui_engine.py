"""Synchronous seam around the existing async ChatGPT browser transport."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any


def _run_sync(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, value).result()


class ChatGptGuiEngine:
    """Adapter expected by ``master_a_dynamic_v4.BrowserAdapter``.

    ``auth_probe`` is deliberately required as an injected capability. If it is
    absent or reports anything other than AUTHENTICATED, the transaction core
    blocks before browser I/O. The adapter never guesses that a browser is
    logged in and never bypasses a challenge.
    """

    def __init__(
        self,
        run_turn: Callable[..., Any],
        *,
        auth_probe: Callable[[str], Any] | None,
        reconcile_probe: Callable[[Mapping[str, Any]], Any] | None = None,
        timeout_seconds: int = 900,
    ):
        self.run_turn = run_turn
        self.auth_probe = auth_probe
        self.reconcile_probe = reconcile_probe
        self.timeout_seconds = int(timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("GUI_TIMEOUT_INVALID")

    def auth_state(self, channel: str) -> dict[str, Any]:
        if self.auth_probe is None:
            return {"status": "AUTHENTICATION_UNAVAILABLE", "channel": channel}
        value = _run_sync(self.auth_probe(channel))
        return dict(value) if isinstance(value, Mapping) else {"status": "AUTH_PROBE_INVALID"}

    def submit(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.loads(str(intent["payload_json"]))
        prompt = str(payload.get("prompt") or "")
        if not prompt:
            raise ValueError("GUI_PROMPT_MISSING")
        run_kwargs = {
            "timeout_seconds": self.timeout_seconds,
            "conversation_url": intent.get("conversation_url"),
        }
        try:
            accepts_actor_kind = "actor_kind" in inspect.signature(self.run_turn).parameters
        except (TypeError, ValueError):
            accepts_actor_kind = False
        if accepts_actor_kind:
            identity = f"{intent.get('channel', '')} {intent.get('actor_id', '')}".lower()
            run_kwargs["actor_kind"] = "WORKER" if "worker" in identity else "MASTER"
        result = _run_sync(self.run_turn(prompt, str(intent["intent_id"]), **run_kwargs))
        if not isinstance(result, (tuple, list)) or len(result) != 3:
            return {"status": "SUBMITTED", "reason": "BROWSER_RESULT_NOT_COMPLETE"}
        response, snapshot, conversation_url = result
        url = str(conversation_url or "").strip()
        if not url or not isinstance(response, Mapping) or not response:
            return {"status": "SUBMITTED", "conversation_url": url, "snapshot": str(snapshot or "")}
        remote_identity = hashlib.sha256(
            json.dumps(
                {"intent_id": intent["intent_id"], "url": url, "response": dict(response)},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "status": "RESPONSE_CAPTURED",
            "conversation_url": url,
            "remote_identity": remote_identity,
            "response": dict(response),
            "snapshot": str(snapshot or ""),
        }

    def reconcile(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        if self.reconcile_probe is None:
            return {"status": "AMBIGUOUS", "reason": "RECONCILE_PROBE_UNAVAILABLE"}
        value = _run_sync(self.reconcile_probe(intent))
        return dict(value) if isinstance(value, Mapping) else {"status": "AMBIGUOUS", "reason": "RECONCILE_PROBE_INVALID"}
