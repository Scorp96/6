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
        intent_id = str(intent.get("intent_id") or "")
        if not url:
            marker = ""
            action_kind = str(intent.get("action_kind") or "")
            if action_kind == "CHATGPT_WORKER_SUBMIT":
                try:
                    payload = json.loads(str(intent.get("payload_json") or "{}"))
                except (TypeError, ValueError):
                    payload = {}
                assignment = payload.get("worker_assignment") if isinstance(payload, Mapping) else None
                if isinstance(assignment, Mapping):
                    marker = str(assignment.get("assignment_id") or "").strip()
            elif action_kind == "MASTER_REASONING":
                # The reasoning prompt carries its deterministic intent id.
                # This lets recovery locate an unpromoted Master turn without
                # ever resubmitting an ambiguous external side effect.
                marker = intent_id
            recover_unpromoted = getattr(driver, "recover_unpromoted_turn_snapshot", None)
            if not marker or not callable(recover_unpromoted):
                return {"status": "AMBIGUOUS", "reason": "CONVERSATION_URL_MISSING"}
            prove_not_submitted = getattr(driver, "prove_turn_not_submitted", None)
            if callable(prove_not_submitted):
                try:
                    proof = prove_not_submitted(intent_id)
                except (OSError, RuntimeError, ValueError):
                    proof = None
                if isinstance(proof, Mapping) and str(proof.get("proof") or "").strip():
                    return {
                        "status": "VERIFIED_NOT_SUBMITTED",
                        "proof": str(proof["proof"]),
                        "observation": dict(proof),
                    }
            try:
                snapshot = await asyncio.wait_for(
                    recover_unpromoted(
                        intent_id,
                        expected_marker=marker,
                        timeout_seconds=float(timeout_seconds),
                    ),
                    timeout=float(timeout_seconds),
                )
            except (TimeoutError, RuntimeError, ValueError) as exc:
                return {
                    "status": "AMBIGUOUS",
                    "reason": str(exc) or type(exc).__name__,
                }
        else:
            snapshot = await asyncio.wait_for(
                driver.snapshot_conversation(url), timeout=float(timeout_seconds)
            )
        text = str(snapshot or "")
        observed_url = extract_conversation_url(text)
        if url and observed_url and observed_url != url:
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
