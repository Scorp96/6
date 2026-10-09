"""Untrusted conversation-record classifier for a future host event source.

The input is ALREADY-OBTAINED JSON. No HTTP, tokens, cookies, Chrome access,
key signing, tool-call execution or browser-sending code is imported here.

A server record's current_node chain gives stronger *diagnostic* evidence
than a quiet DOM, but this parser cannot independently attest its source.
Even a FINISHED_UNATTESTED record must never become HOST_VERIFIED, a signed
terminal receipt, or a continuation/send permission without a separate
authenticated host event producer and physical binding.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping, Any


@dataclass(frozen=True)
class TurnRecordInspection:
    status: str
    reason: str
    response_sha256: str | None = None
    browser_send_authorized: bool = False
    host_terminal_event_verified: bool = False
    physical_identity_verified: bool = False


def inspect_untrusted_conversation_record(
    record: Mapping[str, Any],
    *,
    expected_conversation_id: str,
    expected_user_node_id: str,
) -> TurnRecordInspection:
    """Never return a host-authenticated completion event from page JSON.

    Traverse only current_node -> parents. The latest user node must equal the
    caller's externally verified user-node ID; a previous answer on an old
    fork or an incomplete latest answer is not a terminal completion.
    """
    def deny(reason: str, status: str = "BLOCKED"):
        return TurnRecordInspection(status, reason)

    if not isinstance(record, Mapping):
        return deny("RECORD_SHAPE_INVALID")
    if not isinstance(expected_conversation_id, str) or not expected_conversation_id.strip():
        return deny("EXPECTED_CONVERSATION_ID_REQUIRED")
    if not isinstance(expected_user_node_id, str) or not expected_user_node_id.strip():
        return deny("EXPECTED_USER_NODE_ID_REQUIRED")
    if record.get("conversation_id") != expected_conversation_id:
        return deny("CONVERSATION_ID_MISMATCH")
    mapping = record.get("mapping")
    current = record.get("current_node")
    if not isinstance(mapping, Mapping) or not isinstance(current, str) or not current:
        return deny("LIVE_BRANCH_UNAVAILABLE")
    if len(mapping) > 5000:
        return deny("RECORD_TOO_LARGE")
    # Explicit async/tool activity must defeat an apparently completed text
    # node. Unknown future states also fail closed rather than guessing.
    async_status = record.get("async_status")
    if async_status not in (None, False, "completed", "idle"):
        return deny("ASYNC_WORK_NOT_CLEARED", "INCOMPLETE")

    visited: set[str] = set()
    latest_assistant: Mapping[str, Any] | None = None
    user_id = None
    for _ in range(1000):
        if not isinstance(current, str) or not current:
            break
        if current in visited:
            return deny("LIVE_BRANCH_CYCLE")
        visited.add(current)
        node = mapping.get(current)
        if not isinstance(node, Mapping):
            return deny("LIVE_BRANCH_NODE_INVALID")
        message = node.get("message")
        if isinstance(message, Mapping):
            author = message.get("author")
            role = author.get("role") if isinstance(author, Mapping) else None
            if role == "user":
                user_id = current
                break
            if role == "assistant" and latest_assistant is None:
                content = message.get("content")
                typ = content.get("content_type") if isinstance(content, Mapping) else None
                if typ not in {"reasoning_recap", "thoughts"} and message.get("weight", 1) != 0:
                    latest_assistant = message
        current = node.get("parent")
    else:
        return deny("LIVE_BRANCH_DEPTH_EXCEEDED")

    if user_id is None:
        return deny("LATEST_USER_NODE_NOT_FOUND")
    if user_id != expected_user_node_id:
        return deny("LATEST_USER_ID_MISMATCH")
    if latest_assistant is None:
        return deny("ASSISTANT_REPLY_NOT_FOUND", "INCOMPLETE")
    if latest_assistant.get("end_turn") is not True:
        return deny("SERVER_END_TURN_NOT_CONFIRMED", "INCOMPLETE")

    metadata = latest_assistant.get("metadata")
    details = metadata.get("finish_details") if isinstance(metadata, Mapping) else None
    finish = details.get("type") if isinstance(details, Mapping) else None
    if finish in {"interrupted", "max_tokens", "cancelled"}:
        return deny("ASSISTANT_REPLY_TRUNCATED_OR_INTERRUPTED", "INCOMPLETE")
    # Do not infer that arbitrary or future finish types mean success.
    if finish not in {"stop", "complete", "normal"}:
        return deny("ASSISTANT_FINISH_REASON_UNVERIFIED")

    content = latest_assistant.get("content")
    if not isinstance(content, Mapping):
        return deny("ASSISTANT_CONTENT_INVALID")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > 512:
        return deny("ASSISTANT_CONTENT_UNAVAILABLE")
    text_parts = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, Mapping) and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        else:
            return deny("ASSISTANT_CONTENT_PART_INVALID")
    text = "\n".join(text_parts).strip()
    if not text or len(text) > 2_000_000:
        return deny("ASSISTANT_CONTENT_UNAVAILABLE")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return TurnRecordInspection(
        "FINISHED_UNATTESTED",
        "SERVER_RECORD_STRUCTURALLY_FINISHED_BUT_HOST_PROVENANCE_UNVERIFIED",
        response_sha256=digest,
    )
