import argparse
import asyncio
import ctypes
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3

CANARY_ROOT = Path(r"C:\ScorpAgent\state-v3-live-canary").resolve()


def _validated_root(value):
    root = Path(value).resolve()
    if root == CANARY_ROOT or CANARY_ROOT not in root.parents:
        raise ValueError("CANARY_PROBE_ROOT_OUTSIDE_ALLOWED_BASE")
    transport = root / "actor-transport-v3.json"
    if not transport.is_file():
        raise ValueError("CANARY_PROBE_TRANSPORT_MISSING")
    return root, transport


def _title(hwnd):
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return ""
    size = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(size + 1)
    user32.GetWindowTextW(hwnd, buf, size + 1)
    return buf.value


def _exception_tree(exc):
    row = {"type": type(exc).__name__, "message": str(exc)}
    children = getattr(exc, "exceptions", None)
    if children:
        row["children"] = [_exception_tree(child) for child in children]
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        row["cause"] = _exception_tree(cause)
    return row


async def _run(root, turn_id, close_if_verified):
    root, transport_path = _validated_root(root)
    data = json.loads(transport_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("CANARY_PROBE_TRANSPORT_INVALID")
    row = data.get(turn_id)
    if not isinstance(row, dict):
        raise ValueError("CANARY_PROBE_TURN_MISSING")
    handle = row.get("handle") or {}
    if not isinstance(handle, dict):
        raise ValueError("CANARY_PROBE_HANDLE_INVALID")
    hwnd = handle.get("window_handle")
    url = handle.get("conversation_url") or row.get("conversation_url")
    if type(hwnd) is not int or hwnd <= 0:
        raise ValueError("CANARY_PROBE_HWND_INVALID")
    if not isinstance(url, str) or not url.startswith("https://chatgpt.com/c/"):
        raise ValueError("CANARY_PROBE_URL_INVALID")

    user32 = ctypes.windll.user32
    result = {
        "status": "CANARY_TURN_PROBE",
        "root": str(root),
        "turn_id": turn_id,
        "transport_state": row.get("state"),
        "hwnd": hwnd,
        "url": url,
        "hwnd_alive_before": bool(user32.IsWindow(hwnd)),
        "title_before": _title(hwnd),
        "verified": False,
        "closed": False,
    }
    if not result["hwnd_alive_before"]:
        result["status"] = "CANARY_TURN_ALREADY_CLOSED"
        return result

    driver = WindowsMcpActorDriverV3(
        url_timeout_seconds=15,
        url_poll_seconds=0.5,
        page_wait_seconds=1,
        poll_page_wait_seconds=1,
    )
    try:
        snapshot = await driver.snapshot_conversation(url, window_handle=hwnd)
    except BaseException as exc:
        result["probe_error"] = _exception_tree(exc)
        result["hwnd_alive_after"] = bool(user32.IsWindow(hwnd))
        result["title_after"] = _title(hwnd)
        return result

    lower = snapshot.lower()
    result["verified"] = True
    result["snapshot"] = {
        "length": len(snapshot),
        "throttle": "请求过于频繁" in snapshot or "too many requests" in lower,
        "stop_generating": "Stop generating" in snapshot or "停止回答" in snapshot,
        "actor_marker": f"SCORP_GUI_ACTOR_V3::{turn_id}::" in snapshot,
    }
    if close_if_verified:
        result["closed"] = bool(await driver.close_conversation_window(hwnd))
    result["hwnd_alive_after"] = bool(user32.IsWindow(hwnd))
    result["title_after"] = _title(hwnd)
    if close_if_verified and (not result["closed"] or result["hwnd_alive_after"]):
        raise RuntimeError("CANARY_PROBE_EXACT_CLOSE_FAILED")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--close-if-verified", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(_run(args.root, args.turn_id, args.close_if_verified))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
