import asyncio
import hashlib
import json
import re

from bridge_core import extract_actor_response_v3, response_is_terminal
from gui_transport import (
    extract_conversation_url,
    open_new_chat_and_submit,
    render_actor_prompt_v3,
    result_text,
)
from windows_mcp_actor_driver_v3 import _default_session_factory

TURN_ID = "diag-url-binding-g70"


def _h(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _urls(text):
    return sorted(set(re.findall(r"https://chatgpt\.com/c/[A-Za-z0-9-]+", text or "")))


def _focused_section(text):
    if "Focused Window:" not in (text or ""):
        return ""
    section = text.split("Focused Window:", 1)[1]
    if "Opened Windows:" in section:
        section = section.split("Opened Windows:", 1)[0]
    return section


def _snapshot_summary(text):
    all_urls = _urls(text)
    focused_urls = _urls(_focused_section(text))
    first = extract_conversation_url(text)
    marker = f"SCORP_GUI_ACTOR_V3::{TURN_ID}::"
    return {
        "length": len(text),
        "all_url_count": len(all_urls),
        "all_url_hashes": [_h(x) for x in all_urls],
        "focused_url_count": len(focused_urls),
        "focused_url_hashes": [_h(x) for x in focused_urls],
        "extract_first_hash": _h(first) if first else None,
        "has_turn_prompt": f"TURN_ID={TURN_ID}" in text,
        "has_response_marker": marker in text,
        "terminal": response_is_terminal(text),
        "has_editor": any(x in text for x in ("Message ChatGPT", "与 ChatGPT 聊天")),
    }


async def _snapshot(client):
    result = await client.call_tool("Snapshot", {
        "use_vision": False,
        "use_dom": False,
        "use_annotation": True,
        "use_ui_tree": True,
    })
    if getattr(result, "isError", False):
        raise RuntimeError("SNAPSHOT_ERROR")
    return result_text(result)


async def main():
    envelope = {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": TURN_ID,
        "project_id": "diag-url-binding",
        "state_version": 0,
        "actor_kind": "MASTER",
        "actor_id": "A",
        "session_id": "diag-a-window",
        "root_objective_sha256": "d" * 64,
        "acceptance_sha256": "e" * 64,
        "payload": {
            "event": "DIAGNOSTIC",
            "project_state_version": 0,
            "project_state_sha256": "f" * 64,
            "project_state": {
                "status": "ACTIVE",
                "current_phase": "URL_BINDING_DIAGNOSTIC",
                "next_exact_action": "Return WAIT immediately; do not dispatch workers.",
            },
            "instruction": "For this diagnostic, return kind WAIT immediately and make no other decision.",
        },
    }
    prompt = render_actor_prompt_v3(envelope)

    async with _default_session_factory() as client:
        await open_new_chat_and_submit(client, prompt, page_wait_seconds=3, conversation_url=None)

        first_capture = None
        first_summary = None
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            text = await _snapshot(client)
            url = extract_conversation_url(text)
            if url:
                first_capture = url
                first_summary = _snapshot_summary(text)
                break
            await client.call_tool("Wait", {"duration": 0.5})
        if not first_capture:
            raise RuntimeError("DIAG_FIRST_URL_NOT_CAPTURED")

        final_text = None
        response_kind = None
        deadline = asyncio.get_running_loop().time() + 180
        marker = f"SCORP_GUI_ACTOR_V3::{TURN_ID}::"
        while asyncio.get_running_loop().time() < deadline:
            text = await _snapshot(client)
            if marker in text and response_is_terminal(text):
                final_text = text
                response_kind = extract_actor_response_v3(text, TURN_ID, "MASTER").get("kind")
                break
            await client.call_tool("Wait", {"duration": 1})
        if final_text is None:
            raise RuntimeError("DIAG_TERMINAL_RESPONSE_NOT_OBSERVED")

        final_summary = _snapshot_summary(final_text)
        await client.call_tool("Shortcut", {"shortcut": "alt+f4"})

    final_focused = _urls(_focused_section(final_text))
    conclusion = {
        "turn_id": TURN_ID,
        "response_kind": response_kind,
        "first_capture_hash": _h(first_capture),
        "first": first_summary,
        "final": final_summary,
        "first_capture_in_final_focused": first_capture in final_focused,
        "first_capture_in_final_all": first_capture in _urls(final_text),
    }
    print("SUBMIT_URL_BINDING_DIAG=" + json.dumps(conclusion, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
