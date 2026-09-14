from __future__ import annotations

import argparse
import asyncio
import json

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from chrome_use_cli_v3 import ChromeUseCliV3
from p0_live_chatgpt_probe import recover_persisted_live_ack


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--command-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--transaction-timeout-seconds", type=float, default=20.0)
    return parser.parse_args()


async def run(args):
    cli = ChromeUseCliV3(executable=args.executable)
    driver = ChromeUseActorDriverV3(cli, args.state_path, timeout_seconds=args.command_timeout_seconds)
    before = driver.turn_binding(args.turn_id)
    if not isinstance(before, dict):
        raise ValueError("LIVE_ACK_RECOVERY_BINDING_MISSING")
    before_session = str(before.get("session") or "").strip()
    if not before_session:
        raise ValueError("LIVE_ACK_RECOVERY_BINDING_MISSING")

    result = await recover_persisted_live_ack(
        driver=driver,
        turn_id=args.turn_id,
        token=args.token,
        command_timeout_seconds=args.command_timeout_seconds,
        transaction_timeout_seconds=args.transaction_timeout_seconds,
    )
    after = driver.turn_binding(args.turn_id)
    after_session = str((after or {}).get("session") or "").strip()
    after_url = str((after or {}).get("conversation_url") or "").strip()
    if after_session != before_session:
        raise ValueError("LIVE_ACK_RECOVERY_SESSION_CHANGED")
    if result.get("status") != "VERIFIED":
        raise ValueError("LIVE_ACK_RECOVERY_ACK_NOT_VERIFIED")
    if not after_url.startswith("https://chatgpt.com/c/"):
        raise ValueError("LIVE_ACK_RECOVERY_CANONICAL_URL_MISSING")

    print(json.dumps({
        "status": "PASS",
        "turn_id": args.turn_id,
        "session": after_session,
        "conversation_url": after_url,
        "ack_source": "persisted_session_recovery",
        "no_resubmit": True,
    }, separators=(",", ":")))
    print("G939_NO_RESUBMIT_RECOVERY=PASS")
    print("P0_LIVE_CHATGPT_PROBE=PASS")
    return 0


def main():
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
