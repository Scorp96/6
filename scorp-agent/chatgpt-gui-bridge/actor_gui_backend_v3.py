from __future__ import annotations

import re

from bridge_core import extract_actor_response_v3, response_is_terminal
from gui_transport import extract_conversation_url, validate_conversation_url


_ALLOWED_ACTORS = {"MASTER", "WORKER"}


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


def _actor_kind(value) -> str:
    kind = str(value or "").strip().upper()
    if kind not in _ALLOWED_ACTORS:
        raise ValueError("ACTOR_GUI_ACTOR_KIND_INVALID")
    return kind


def _canonical_url(value) -> str:
    value = _text(value, "ACTOR_GUI_CONVERSATION_URL_MISSING")
    validated = validate_conversation_url(value)
    if validated == "https://chatgpt.com/":
        raise ValueError("ACTOR_GUI_CONVERSATION_URL_MISSING")
    return validated


def _exception_leaves(exc):
    children = getattr(exc, "exceptions", None)
    if isinstance(children, tuple):
        leaves = []
        for child in children:
            leaves.extend(_exception_leaves(child))
        return leaves
    return [exc]


def _recoverable_window_binding_error(leaf) -> bool:
    return (
        isinstance(leaf, ValueError)
        and str(leaf) in {
            "ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH",
            "CHROME_USE_WINDOW_HANDLE_UNSUPPORTED",
        }
    ) or (
        isinstance(leaf, RuntimeError)
        and str(leaf) == "ACTOR_GUI_WINDOW_FOCUS_FAILED"
    )


def _only_recoverable_window_binding_errors(exc) -> bool:
    leaves = _exception_leaves(exc)
    return bool(leaves) and all(_recoverable_window_binding_error(leaf) for leaf in leaves)


class ActorGuiBackendV3:
    """Nonblocking actor backend over an injected GUI driver.

    submit() returns once the prompt has a canonical ChatGPT conversation URL.
    poll() performs one observation only. recover() never submits; it accepts
    exactly one discovered conversation bound to the requested turn.
    """

    def __init__(self, driver):
        self.driver = driver

    async def submit(
        self,
        submission_id,
        *,
        prompt,
        turn_id,
        actor_kind,
        conversation_url,
        timeout_seconds,
    ) -> dict:
        submission_id = _text(submission_id, "ACTOR_GUI_SUBMISSION_ID_MISSING")
        prompt = _text(prompt, "ACTOR_GUI_PROMPT_MISSING")
        turn_id = _text(turn_id, "ACTOR_GUI_TURN_ID_MISSING")
        actor_kind = _actor_kind(actor_kind)
        if conversation_url is not None:
            conversation_url = _canonical_url(conversation_url)
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds <= 0:
            raise ValueError("ACTOR_GUI_TIMEOUT_INVALID")

        snapshot = await self.driver.submit_prompt(
            prompt=prompt,
            turn_id=turn_id,
            actor_kind=actor_kind,
            conversation_url=conversation_url,
        )
        if not isinstance(snapshot, str):
            raise ValueError("ACTOR_GUI_SUBMIT_SNAPSHOT_INVALID")
        window_handle = getattr(snapshot, "window_handle", None)
        if window_handle is not None and (type(window_handle) is not int or window_handle <= 0):
            raise ValueError("ACTOR_GUI_WINDOW_HANDLE_INVALID")
        canonical_url = extract_conversation_url(snapshot)
        if not canonical_url:
            raise ValueError("ACTOR_GUI_CONVERSATION_URL_MISSING")
        canonical_url = _canonical_url(canonical_url)
        handle = {
            "submission_id": submission_id,
            "turn_id": turn_id,
            "actor_kind": actor_kind,
            "conversation_url": canonical_url,
        }
        if window_handle is not None:
            handle["window_handle"] = window_handle
        return handle

    async def poll(self, handle, *, turn_id, actor_kind, timeout_seconds) -> dict:
        if not isinstance(handle, dict):
            raise ValueError("ACTOR_GUI_HANDLE_INVALID")
        turn_id = _text(turn_id, "ACTOR_GUI_TURN_ID_MISSING")
        actor_kind = _actor_kind(actor_kind)
        if handle.get("turn_id") != turn_id:
            raise ValueError("ACTOR_GUI_HANDLE_TURN_CONFLICT")
        handle_kind = str(handle.get("actor_kind") or actor_kind).upper()
        if handle_kind != actor_kind:
            raise ValueError("ACTOR_GUI_HANDLE_ACTOR_CONFLICT")
        _text(handle.get("submission_id"), "ACTOR_GUI_SUBMISSION_ID_MISSING")
        url = _canonical_url(handle.get("conversation_url"))
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds <= 0:
            raise ValueError("ACTOR_GUI_TIMEOUT_INVALID")

        window_handle = handle.get("window_handle")
        stale_window_handle = False
        if window_handle is None:
            snapshot = await self.driver.snapshot_conversation(url)
        else:
            if type(window_handle) is not int or window_handle <= 0:
                raise ValueError("ACTOR_GUI_WINDOW_HANDLE_INVALID")
            try:
                snapshot = await self.driver.snapshot_conversation(url, window_handle=window_handle)
            except Exception as exc:
                if not _only_recoverable_window_binding_errors(exc):
                    raise
                stale_window_handle = True
                snapshot = await self.driver.snapshot_conversation(url)
        if not isinstance(snapshot, str):
            raise ValueError("ACTOR_GUI_POLL_SNAPSHOT_INVALID")
        marker = f"SCORP_GUI_ACTOR_V3::{turn_id}::"
        if marker not in snapshot or not response_is_terminal(snapshot):
            return {"status": "PENDING"}
        try:
            response = extract_actor_response_v3(snapshot, turn_id, actor_kind)
        except ValueError as exc:
            if str(exc) == "ACTOR_RESPONSE_INVALID":
                return {"status": "PENDING"}
            raise
        if window_handle is not None and not stale_window_handle:
            close_fn = getattr(self.driver, "close_conversation_window", None)
            if close_fn is None:
                raise RuntimeError("ACTOR_GUI_WINDOW_CLOSE_UNAVAILABLE")
            closed = await close_fn(window_handle)
            if closed is not True:
                raise RuntimeError("ACTOR_GUI_WINDOW_CLOSE_FAILED")
        return {
            "status": "COMPLETED",
            "response": response,
            "snapshot": snapshot,
            "conversation_url": url,
        }

    async def recover(self, submission_id, *, turn_id, actor_kind):
        submission_id = _text(submission_id, "ACTOR_GUI_SUBMISSION_ID_MISSING")
        turn_id = _text(turn_id, "ACTOR_GUI_TURN_ID_MISSING")
        actor_kind = _actor_kind(actor_kind)
        discovered = await self.driver.discover_turn_conversations(turn_id)
        if not isinstance(discovered, list):
            raise ValueError("ACTOR_GUI_RECOVERY_RESULT_INVALID")

        by_url = {}
        turn_pattern = re.compile(rf"(?:TURN_ID\s*=\s*{re.escape(turn_id)}\b|SCORP_GUI_ACTOR_V3::{re.escape(turn_id)}::)")
        for item in discovered:
            if not isinstance(item, dict):
                raise ValueError("ACTOR_GUI_RECOVERY_RESULT_INVALID")
            snapshot = str(item.get("snapshot") or "")
            if not turn_pattern.search(snapshot):
                continue
            url = item.get("conversation_url") or extract_conversation_url(snapshot)
            if not url:
                continue
            url = _canonical_url(url)
            by_url[url] = item

        if not by_url:
            return None
        if len(by_url) != 1:
            raise ValueError("ACTOR_GUI_RECOVERY_AMBIGUOUS")
        url = next(iter(by_url))
        return {
            "submission_id": submission_id,
            "turn_id": turn_id,
            "actor_kind": actor_kind,
            "conversation_url": url,
        }
