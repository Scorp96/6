import asyncio
import json
from pathlib import Path

from probe_canary_turn_v3 import _run

ROOT = Path(r"C:\ScorpAgent\state-v3-live-canary\parallel-g257")
W2 = "v3-worker-d10d433009adbcef6694"
A_CORE = "v3-master-core-789bf822a15feb566918"


async def main():
    w2 = await _run(ROOT, W2, False)
    a_core = await _run(ROOT, A_CORE, True)
    if not a_core.get("verified") or not a_core.get("closed") or a_core.get("hwnd_alive_after"):
        raise RuntimeError("A_CORE_CLOSE_NOT_PROVEN")
    print(json.dumps({"status": "G257_TARGETED_PROBE", "w2": w2, "a_core": a_core}, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
