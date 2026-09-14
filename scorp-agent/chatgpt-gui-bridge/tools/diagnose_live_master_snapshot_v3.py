import asyncio
import hashlib
import json
from pathlib import Path

from bridge_core import extract_actor_response_v3, response_is_terminal
from gui_transport import extract_conversation_url, focused_window_is_chrome
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3

ROOT = Path(r"C:\ScorpAgent\state-v3-live-canary\parallel-g63")


def _h(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


async def main():
    rows = json.loads((ROOT / "actor-transport-v3.json").read_text(encoding="utf-8-sig"))
    if len(rows) != 1:
        raise RuntimeError("EXPECTED_ONE_TRANSPORT_ROW")
    turn_id, row = next(iter(rows.items()))
    handle = row.get("handle") or {}
    url = handle.get("conversation_url")
    if not url:
        raise RuntimeError("CONVERSATION_URL_MISSING")
    driver = WindowsMcpActorDriverV3(poll_page_wait_seconds=3)
    snapshot = await driver.snapshot_conversation(url)
    observed = extract_conversation_url(snapshot)
    marker = f"SCORP_GUI_ACTOR_V3::{turn_id}::"
    response_kind = None
    parse_error = None
    if marker in snapshot:
        try:
            response_kind = extract_actor_response_v3(snapshot, turn_id, "MASTER").get("kind")
        except Exception as exc:
            parse_error = type(exc).__name__ + ":" + str(exc)
    lower = snapshot.lower()
    summary = {
        "turn_id": turn_id,
        "snapshot_length": len(snapshot),
        "focused_chrome": focused_window_is_chrome(snapshot),
        "expected_url_hash": _h(url),
        "observed_url_present": bool(observed),
        "observed_url_hash": _h(observed) if observed else None,
        "url_match": observed == url,
        "has_turn_prompt": f"TURN_ID={turn_id}" in snapshot,
        "has_actor_response_marker": marker in snapshot,
        "response_terminal": response_is_terminal(snapshot),
        "response_kind": response_kind,
        "parse_error": parse_error,
        "has_stop_generating": any(x in snapshot for x in ("Stop generating", "Stop responding", "停止回答")),
        "has_retry_signal": any(x in lower for x in ("retry", "regenerate", "try again", "重试")),
        "has_error_signal": any(x in lower for x in ("something went wrong", "network error", "出了点问题", "网络错误")),
        "has_message_editor": any(x in snapshot for x in ("Message ChatGPT", "与 ChatGPT 聊天")),
    }
    print("LIVE_MASTER_SNAPSHOT_DIAG=" + json.dumps(summary, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
