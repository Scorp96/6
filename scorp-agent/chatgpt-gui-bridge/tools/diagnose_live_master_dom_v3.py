import asyncio
import hashlib
import json
from pathlib import Path

from bridge_core import extract_actor_response_v3, response_is_terminal
from gui_transport import acquire_chatgpt_window, extract_conversation_url, result_text
from windows_mcp_actor_driver_v3 import _default_session_factory

ROOT = Path(r"C:\ScorpAgent\state-v3-live-canary\parallel-g63")


def _h(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _summarize(snapshot, turn_id, actor_kind, expected_url):
    marker = f"SCORP_GUI_ACTOR_V3::{turn_id}::"
    response_kind = None
    parse_error = None
    if marker in snapshot:
        try:
            response_kind = extract_actor_response_v3(snapshot, turn_id, actor_kind).get("kind")
        except Exception as exc:
            parse_error = type(exc).__name__ + ":" + str(exc)
    observed = extract_conversation_url(snapshot)
    return {
        "snapshot_length": len(snapshot),
        "observed_url_present": bool(observed),
        "observed_url_hash": _h(observed) if observed else None,
        "url_match": observed == expected_url,
        "has_turn_prompt": f"TURN_ID={turn_id}" in snapshot,
        "has_actor_response_marker": marker in snapshot,
        "response_terminal": response_is_terminal(snapshot),
        "response_kind": response_kind,
        "parse_error": parse_error,
        "has_message_editor": any(x in snapshot for x in ("Message ChatGPT", "与 ChatGPT 聊天")),
        "has_stop_generating": any(x in snapshot for x in ("Stop generating", "Stop responding", "停止回答")),
    }


async def main():
    rows = json.loads((ROOT / "actor-transport-v3.json").read_text(encoding="utf-8-sig"))
    if len(rows) != 1:
        raise RuntimeError("EXPECTED_ONE_TRANSPORT_ROW")
    turn_id, row = next(iter(rows.items()))
    handle = row.get("handle") or {}
    url = handle.get("conversation_url")
    actor_kind = (row.get("binding") or {}).get("actor_kind") or "MASTER"
    if not url:
        raise RuntimeError("CONVERSATION_URL_MISSING")

    async with _default_session_factory() as client:
        await acquire_chatgpt_window(client, wait_seconds=5, conversation_url=url)
        await client.call_tool("Wait", {"duration": 3})
        ui_result = await client.call_tool("Snapshot", {
            "use_vision": False,
            "use_dom": False,
            "use_annotation": True,
            "use_ui_tree": True,
        })
        dom_result = await client.call_tool("Snapshot", {
            "use_vision": False,
            "use_dom": True,
            "use_annotation": False,
            "use_ui_tree": False,
        })
        if getattr(ui_result, "isError", False):
            raise RuntimeError("UI_SNAPSHOT_ERROR")
        if getattr(dom_result, "isError", False):
            raise RuntimeError("DOM_SNAPSHOT_ERROR")
        ui = result_text(ui_result)
        dom = result_text(dom_result)
        await client.call_tool("Shortcut", {"shortcut": "alt+f4"})

    summary = {
        "turn_id": turn_id,
        "expected_url_hash": _h(url),
        "ui": _summarize(ui, turn_id, actor_kind, url),
        "dom": _summarize(dom, turn_id, actor_kind, url),
    }
    print("LIVE_MASTER_DOM_COMPARE=" + json.dumps(summary, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
