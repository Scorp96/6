import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gui_transport import render_controller_prompt, run_live_turn

REQ = {
    "request_id": "cont-gui-live-canary-001",
    "task_id": "gui-live-canary",
    "project_id": "chatgpt-gui-bridge-v1",
    "registry_sequence": 0,
    "continuation_generation": 0,
    "question": "This is a harmless transport canary. Return SET_TERMINAL with state DONE.",
    "safety_class": "standard",
    "previous_action_id": None,
    "previous_result_sha256": None,
}

async def main():
    prompt = render_controller_prompt(REQ, {"status": "CANARY"})
    mutation, snapshot, url = await run_live_turn(prompt, REQ["request_id"], 180)
    print("GUI_CANARY_MUTATION=" + json.dumps(mutation, ensure_ascii=False, separators=(",", ":")))
    print("GUI_CANARY_URL=" + str(url))
    if mutation.get("kind") != "SET_TERMINAL" or mutation.get("state") != "DONE":
        raise SystemExit("GUI_CANARY_WRONG_MUTATION")
    if not url or "/c/" not in url:
        raise SystemExit("GUI_CANARY_NO_CONVERSATION_URL")
    print("CHATGPT_GUI_TRANSPORT_CANARY_PASS")

if __name__ == "__main__":
    asyncio.run(main())
