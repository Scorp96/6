from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from chrome_use_cli_v3 import ChromeUseCliV3
from p0_live_chatgpt_probe import recover_persisted_live_ack
from session_registry_v3 import SessionRegistryV3
from worker_conversation_pool_v3 import WorkerConversationPoolV3


async def _submit_exact(driver, *, turn_id, actor_kind, token, conversation_url=None):
    prompt = f"Reply with exactly this one line and nothing else: {token}"
    submit_error = None
    try:
        await driver.submit_prompt(
            prompt=prompt,
            turn_id=turn_id,
            actor_kind=actor_kind,
            conversation_url=conversation_url,
        )
    except Exception as exc:
        submit_error = f"{type(exc).__name__}:{exc}"

    last = None
    for _ in range(12):
        try:
            last = await recover_persisted_live_ack(
                driver=driver,
                turn_id=turn_id,
                token=token,
                command_timeout_seconds=5,
                transaction_timeout_seconds=20,
            )
        except Exception:
            last = None
        if isinstance(last, dict) and last.get("status") == "VERIFIED":
            return {**last, "submit_error": submit_error}
        await asyncio.sleep(5)
    raise RuntimeError(f"PERSISTENT_SESSION_ACK_NOT_VERIFIED:{turn_id}:{submit_error}:{last}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--driver-state", required=True)
    parser.add_argument("--registry-state", required=True)
    parser.add_argument("--worker-pool-state", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--prefix", required=True)
    return parser.parse_args()


async def run(args):
    for raw in (args.driver_state, args.registry_state, args.worker_pool_state):
        if Path(raw).exists():
            raise RuntimeError(f"PERSISTENT_SESSION_FRESH_STATE_REQUIRED:{raw}")

    cli = ChromeUseCliV3(executable=args.executable)
    driver = ChromeUseActorDriverV3(cli, args.driver_state, timeout_seconds=30)
    registry = SessionRegistryV3(args.registry_state)
    pool = WorkerConversationPoolV3(args.worker_pool_state, pool_size=1)

    # Master A: two logical A windows must share one persistent conversation/session.
    a1_turn = f"{args.prefix}-a1"
    a2_turn = f"{args.prefix}-a2"
    a1_window = f"{args.prefix}-a-window-1"
    a2_window = f"{args.prefix}-a-window-2"
    a1 = await _submit_exact(
        driver,
        turn_id=a1_turn,
        actor_kind="MASTER",
        token=f"SCORP_PERSIST_A1::{args.prefix}",
    )
    master_url = a1["conversation_url"]
    registry.record_session(args.project_id, a1_window, "MASTER", "A", a1_turn, master_url)
    resolved_master_url = registry.resolve_actor_conversation(args.project_id, a2_window, "MASTER", "A")
    if resolved_master_url != master_url:
        raise RuntimeError("PERSISTENT_MASTER_URL_NOT_RESOLVED")
    a2 = await _submit_exact(
        driver,
        turn_id=a2_turn,
        actor_kind="MASTER",
        token=f"SCORP_PERSIST_A2::{args.prefix}",
        conversation_url=resolved_master_url,
    )
    registry.record_session(args.project_id, a2_window, "MASTER", "A", a2_turn, a2["conversation_url"])
    a1_binding = driver.turn_binding(a1_turn)
    a2_binding = driver.turn_binding(a2_turn)
    if a1_binding.get("conversation_url") != a2_binding.get("conversation_url"):
        raise RuntimeError("PERSISTENT_MASTER_CONVERSATION_CHANGED")
    if a1_binding.get("session") != a2_binding.get("session"):
        raise RuntimeError("PERSISTENT_MASTER_CHROME_SESSION_CHANGED")

    # Worker pool: release one logical worker and lease a new one into the same slot/url/session.
    w1_turn = f"{args.prefix}-w1"
    w2_turn = f"{args.prefix}-w2"
    w1_actor = "worker-persist-01"
    w2_actor = "worker-persist-02"
    w1_session = f"{args.prefix}-worker-session-1"
    w2_session = f"{args.prefix}-worker-session-2"
    slot1 = pool.acquire(args.project_id, w1_actor, w1_session, w1_turn)
    if slot1 is None or slot1.get("conversation_url") is not None:
        raise RuntimeError("PERSISTENT_WORKER_FIRST_SLOT_INVALID")
    w1 = await _submit_exact(
        driver,
        turn_id=w1_turn,
        actor_kind="WORKER",
        token=f"SCORP_PERSIST_W1::{args.prefix}",
    )
    pool.bind_conversation(args.project_id, w1_actor, w1_session, w1_turn, w1["conversation_url"])
    released1 = pool.release(args.project_id, w1_actor, w1_session, w1_turn)
    slot2 = pool.acquire(args.project_id, w2_actor, w2_session, w2_turn)
    if slot2 is None or slot2.get("slot_id") != slot1.get("slot_id"):
        raise RuntimeError("PERSISTENT_WORKER_SLOT_CHANGED")
    if slot2.get("conversation_url") != released1.get("conversation_url"):
        raise RuntimeError("PERSISTENT_WORKER_URL_NOT_REUSED")
    w2 = await _submit_exact(
        driver,
        turn_id=w2_turn,
        actor_kind="WORKER",
        token=f"SCORP_PERSIST_W2::{args.prefix}",
        conversation_url=slot2["conversation_url"],
    )
    pool.bind_conversation(args.project_id, w2_actor, w2_session, w2_turn, w2["conversation_url"])
    pool.release(args.project_id, w2_actor, w2_session, w2_turn)
    w1_binding = driver.turn_binding(w1_turn)
    w2_binding = driver.turn_binding(w2_turn)
    if w1_binding.get("conversation_url") != w2_binding.get("conversation_url"):
        raise RuntimeError("PERSISTENT_WORKER_CONVERSATION_CHANGED")
    if w1_binding.get("session") != w2_binding.get("session"):
        raise RuntimeError("PERSISTENT_WORKER_CHROME_SESSION_CHANGED")

    state = json.loads(Path(args.driver_state).read_text(encoding="utf-8-sig"))
    if len(state.get("conversations") or {}) != 2:
        raise RuntimeError("PERSISTENT_SESSION_CONVERSATION_COUNT_UNBOUNDED")

    print(json.dumps({
        "status": "PASS",
        "project_id": args.project_id,
        "master": {
            "conversation_url": master_url,
            "chrome_session": a1_binding.get("session"),
            "logical_turns": [a1_turn, a2_turn],
            "logical_windows": [a1_window, a2_window],
        },
        "worker": {
            "slot_id": slot1.get("slot_id"),
            "conversation_url": w1_binding.get("conversation_url"),
            "chrome_session": w1_binding.get("session"),
            "logical_turns": [w1_turn, w2_turn],
            "logical_workers": [w1_actor, w2_actor],
        },
        "driver_conversation_count": len(state.get("conversations") or {}),
    }, separators=(",", ":")))
    print("P0_PERSISTENT_A_WORKER_SESSIONS=PASS")
    return 0


def main():
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
