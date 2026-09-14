import asyncio
import json
import sys
import traceback
from pathlib import Path

PROD_MODULES = Path(r"C:\ScorpAgent\chatgpt-gui-bridge")
ROOT = Path(r"C:\ScorpAgent\state-v3\active")
if str(PROD_MODULES) not in sys.path:
    sys.path.insert(0, str(PROD_MODULES))

from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def flatten(exc, depth=0):
    row = {
        "depth": depth,
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-8000:],
    }
    out = [row]
    children = getattr(exc, "exceptions", None)
    if isinstance(children, tuple):
        for child in children:
            out.extend(flatten(child, depth + 1))
    return out


async def main():
    transport = read_json(ROOT / "actor-transport-v3.json")
    targets = []
    for turn_id, row in transport.items():
        binding = (row or {}).get("binding") or {}
        if binding.get("actor_kind") == "WORKER" and (row or {}).get("state") == "SUBMITTED":
            handle = (row or {}).get("handle") or {}
            targets.append((turn_id, handle))
    if len(targets) != 1:
        raise RuntimeError("EXPECTED_ONE_SUBMITTED_WORKER:" + str(len(targets)))
    turn_id, handle = targets[0]
    url = handle.get("conversation_url")
    hwnd = handle.get("window_handle")
    driver = WindowsMcpActorDriverV3()
    try:
        snapshot = await driver.snapshot_conversation(url, window_handle=hwnd)
        print(json.dumps({
            "status": "SNAPSHOT_OK",
            "turn_id": turn_id,
            "window_handle": hwnd,
            "conversation_url": url,
            "snapshot_tail": str(snapshot)[-4000:],
        }, ensure_ascii=False, separators=(",", ":")))
    except BaseException as exc:
        print(json.dumps({
            "status": "SNAPSHOT_ERROR",
            "turn_id": turn_id,
            "window_handle": hwnd,
            "conversation_url": url,
            "exceptions": flatten(exc),
        }, ensure_ascii=False, separators=(",", ":")))
        raise


if __name__ == "__main__":
    asyncio.run(main())
