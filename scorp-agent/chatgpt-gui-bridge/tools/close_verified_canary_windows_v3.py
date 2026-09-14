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


def _title(hwnd):
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return ""
    size = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(size + 1)
    user32.GetWindowTextW(hwnd, buf, size + 1)
    return buf.value


def _visible_chrome():
    user32 = ctypes.windll.user32
    rows = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _title(hwnd)
        if "Google Chrome" in title:
            rows.append({"hwnd": int(hwnd), "title": title})
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return rows


def _validated_root(value):
    root = Path(value).resolve()
    if root == CANARY_ROOT or CANARY_ROOT not in root.parents:
        raise ValueError("CANARY_CLEAN_ROOT_OUTSIDE_ALLOWED_BASE")
    transport = root / "actor-transport-v3.json"
    if not transport.is_file():
        raise ValueError("CANARY_CLEAN_TRANSPORT_MISSING")
    return root, transport


async def _run(root):
    root, transport = _validated_root(root)
    data = json.loads(transport.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("CANARY_CLEAN_TRANSPORT_INVALID")

    driver = WindowsMcpActorDriverV3(
        url_timeout_seconds=15,
        url_poll_seconds=0.5,
        page_wait_seconds=1,
        poll_page_wait_seconds=1,
    )
    user32 = ctypes.windll.user32
    results = []

    for turn_id, row in data.items():
        if not isinstance(row, dict):
            continue
        handle = row.get("handle") or {}
        if not isinstance(handle, dict):
            continue
        hwnd = handle.get("window_handle")
        url = handle.get("conversation_url") or row.get("conversation_url")
        if type(hwnd) is not int or hwnd <= 0 or not user32.IsWindow(hwnd):
            continue
        if not isinstance(url, str) or not url.startswith("https://chatgpt.com/c/"):
            raise RuntimeError("CANARY_CLEAN_CANONICAL_URL_MISSING")

        result = {
            "turn_id": str(turn_id),
            "hwnd": hwnd,
            "url": url,
            "title_before": _title(hwnd),
        }
        snapshot = await driver.snapshot_conversation(url, window_handle=hwnd)
        result["verified"] = True
        result["throttle"] = (
            "请求过于频繁" in snapshot or "too many requests" in snapshot.lower()
        )
        closed = await driver.close_conversation_window(hwnd)
        result["closed"] = closed is True
        result["alive_after"] = bool(user32.IsWindow(hwnd))
        if closed is not True or result["alive_after"]:
            raise RuntimeError("CANARY_CLEAN_EXACT_CLOSE_FAILED")
        results.append(result)

    return {
        "status": "VERIFIED_CANARY_WINDOWS_CLOSED",
        "root": str(root),
        "closed_count": len(results),
        "results": results,
        "visible_chrome_after": _visible_chrome(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args.root)), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
