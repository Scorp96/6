import asyncio
import contextlib
import hashlib
import json
import os
import re

from bridge_core import response_is_terminal
from gui_transport import open_new_chat_and_submit, render_actor_prompt_v3, result_text

URL_RE = re.compile(r"https://chatgpt\.com(?:/c/[A-Za-z0-9_-]+)?/?")


def _hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16] if value else None


@contextlib.asynccontextmanager
async def session_factory():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = dict(os.environ)
    env["ANONYMIZED_TELEMETRY"] = "false"
    env["WINDOWS_MCP_DISABLE_FLASH"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    server = StdioServerParameters(command="uvx", args=["--from", "windows-mcp==0.8.5", "windows-mcp", "serve", "--tools", "App,Shortcut,Clipboard,Click,Wait,Snapshot"], env=env)
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


async def snapshot(client):
    row = await client.call_tool("Snapshot", {"use_vision": False, "use_dom": False, "use_annotation": True, "use_ui_tree": True})
    return result_text(row)


async def address_observation(client):
    await client.call_tool("Shortcut", {"shortcut": "ctrl+l"})
    await client.call_tool("Shortcut", {"shortcut": "ctrl+c"})
    clip = await client.call_tool("Clipboard", {"mode": "get"})
    raw = result_text(clip)
    await client.call_tool("Shortcut", {"shortcut": "esc"})
    matches = URL_RE.findall(raw)
    unique = sorted(set(matches))
    return {
        "raw_hash": _hash(raw),
        "url_count": len(unique),
        "url_hashes": [_hash(x) for x in unique],
        "chat_count": sum(1 for x in unique if "/c/" in x),
    }


async def main():
    turn_id = "diag-address-regex-g78"
    envelope = {
        "protocol_version": "scorp.master-worker/turn-v3",
        "turn_id": turn_id,
        "project_id": "diag-address-regex",
        "state_version": 0,
        "actor_kind": "MASTER",
        "actor_id": "A",
        "session_id": "diag-address-regex-window",
        "root_objective_sha256": "d" * 64,
        "acceptance_sha256": "e" * 64,
        "payload": {"event": "DIAGNOSTIC", "project_state": {"project_id": "diag-address-regex", "state_version": 0, "status": "ACTIVE", "next_exact_action": "Return WAIT immediately with empty state_patch."}},
    }
    prompt = render_actor_prompt_v3(envelope)
    rows = []
    async with session_factory() as client:
        before = await address_observation(client)
        await open_new_chat_and_submit(client, prompt, page_wait_seconds=3, conversation_url=None)
        for index in range(15):
            text = await snapshot(client)
            obs = await address_observation(client)
            rows.append({"i": index, **obs, "has_turn_prompt": f"TURN_ID={turn_id}" in text, "has_response_marker": f"SCORP_GUI_ACTOR_V3::{turn_id}::" in text, "terminal": response_is_terminal(text)})
            if rows[-1]["has_response_marker"] and rows[-1]["terminal"]:
                break
            await client.call_tool("Wait", {"duration": 1})
        await client.call_tool("Shortcut", {"shortcut": "alt+f4"})
    print("ADDRESS_BAR_REGEX_BINDING_DIAG=" + json.dumps({"before": before, "rows": rows}, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
