from __future__ import annotations

import argparse
import asyncio
import json

from relay_cdp_cli_v1 import RelayCdpCliV1


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default="node.exe")
    parser.add_argument("--helper", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--session", required=True)
    return parser.parse_args()


async def run(args):
    cli = RelayCdpCliV1(
        node_executable=args.node,
        helper_path=args.helper,
        state_path=args.state_path,
    )
    await cli.run_json(args.session, "open", "https://chatgpt.com/", timeout_seconds=12)
    url = await cli.run_json(args.session, "get", "url", timeout_seconds=8)
    snapshot = await cli.run_json(args.session, "snapshot", "-i", timeout_seconds=15)
    refs = snapshot.get("data", {}).get("refs", {})
    editor = any(str(meta.get("role") or "").lower() == "textbox" for meta in refs.values() if isinstance(meta, dict))
    if not editor:
        raise RuntimeError("RELAY_TRANSPORT_EDITOR_MISSING")
    print(json.dumps({"status": "PASS", "url": url.get("data", {}).get("url"), "editor": True}, separators=(",", ":")))
    print("P0_RELAY_TRANSPORT_SMOKE=PASS")
    return 0


def main():
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
