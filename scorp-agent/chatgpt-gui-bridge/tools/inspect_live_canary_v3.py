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
        raise ValueError("CANARY_INSPECT_ROOT_OUTSIDE_ALLOWED_BASE")
    if not root.is_dir():
        raise ValueError("CANARY_INSPECT_ROOT_MISSING")
    return root


def _load(root, name, default=None):
    path = root / name
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _title(hwnd):
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return ""
    size = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(size + 1)
    user32.GetWindowTextW(hwnd, buf, size + 1)
    return buf.value


def _json_files(path):
    rows = []
    if not path.is_dir():
        return rows
    for file in sorted(path.glob("*.json")):
        try:
            rows.append(json.loads(file.read_text(encoding="utf-8-sig")))
        except Exception as exc:
            rows.append({"path": str(file), "error": type(exc).__name__ + ":" + str(exc)})
    return rows


async def inspect(root):
    root = _validated_root(root)
    state = _load(root, "project-state.json")
    ledger = _load(root, "master-worker-v3-ledger.json", {}) or {}
    transport = _load(root, "actor-transport-v3.json", {}) or {}
    sessions = _load(root, "sessions-v3.json", {}) or {}
    journal = _load(root, "actor-response-journal-v3.json", {}) or {}
    user32 = ctypes.windll.user32
    driver = WindowsMcpActorDriverV3(
        url_timeout_seconds=15,
        url_poll_seconds=0.5,
        page_wait_seconds=1,
        poll_page_wait_seconds=1,
    )
    rows = []
    for turn_id, row in transport.items():
        row = row or {}
        handle = row.get("handle") or {}
        hwnd = handle.get("window_handle") if isinstance(handle, dict) else None
        url = (handle.get("conversation_url") if isinstance(handle, dict) else None) or row.get("conversation_url")
        item = {
            "turn_id": str(turn_id),
            "transport_state": row.get("state"),
            "submission_id": row.get("submission_id"),
            "handle": handle,
            "ledger": ledger.get(turn_id),
            "journal": journal.get(turn_id),
        }
        if type(hwnd) is int and hwnd > 0:
            item["hwnd_alive"] = bool(user32.IsWindow(hwnd))
            item["title"] = _title(hwnd)
            if item["hwnd_alive"] and isinstance(url, str) and url.startswith("https://chatgpt.com/c/"):
                try:
                    snapshot = await driver.snapshot_conversation(url, window_handle=hwnd)
                    lower = snapshot.lower()
                    item["snapshot"] = {
                        "verified_url": True,
                        "throttle": "请求过于频繁" in snapshot or "too many requests" in lower,
                        "stop_generating": "Stop generating" in snapshot or "停止回答" in snapshot,
                        "actor_marker": f"SCORP_GUI_ACTOR_V3::{turn_id}::" in snapshot,
                        "length": len(snapshot),
                    }
                except Exception as exc:
                    item["snapshot"] = {"error": type(exc).__name__ + ":" + str(exc)}
        rows.append(item)
    return {
        "root": str(root),
        "state": state,
        "ledger": ledger,
        "transport_rows": rows,
        "sessions": sessions,
        "outbox": _json_files(root / "master-worker-v3-outbox"),
        "inbox": _json_files(root / "master-worker-v3-inbox"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(inspect(args.root)), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
