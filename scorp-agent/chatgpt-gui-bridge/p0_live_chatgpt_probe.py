from __future__ import annotations

import argparse
import asyncio
import json
import time

from chrome_use_cli_v3 import ChromeUseCliV3
from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from relay_cdp_cli_v1 import RelayCdpCliV1


def assistant_reply_matches_token(conversation: str, token: str) -> bool:
    text = str(conversation or "")
    expected = str(token or "").strip()
    if not expected:
        return False

    markers = ("#### ChatGPT 说：", "#### ChatGPT said:")
    positions = [(text.rfind(marker), marker) for marker in markers]
    start, marker = max(positions, key=lambda item: item[0])
    if start < 0:
        return False

    reply = text[start + len(marker) :]
    for line in reply.splitlines():
        value = line.strip()
        if value:
            return value == expected
    return False


async def recover_persisted_live_ack(
    *,
    driver,
    turn_id,
    token,
    command_timeout_seconds=5.0,
    transaction_timeout_seconds=20.0,
):
    turn_id = str(turn_id or "").strip()
    token = str(token or "").strip()
    if not turn_id or not token:
        raise ValueError("LIVE_ACK_RECOVERY_INPUT_MISSING")
    command_timeout_seconds = float(command_timeout_seconds)
    transaction_timeout_seconds = float(transaction_timeout_seconds)
    if command_timeout_seconds <= 0 or transaction_timeout_seconds <= 0:
        raise ValueError("LIVE_ACK_RECOVERY_TIMEOUT_INVALID")

    binding = driver.turn_binding(turn_id)
    if not isinstance(binding, dict):
        raise ValueError("LIVE_ACK_RECOVERY_BINDING_MISSING")
    session = str(binding.get("session") or "").strip()
    conversation_url = str(binding.get("conversation_url") or "").strip()
    if not session:
        raise ValueError("LIVE_ACK_RECOVERY_BINDING_MISSING")

    try:
        if conversation_url:
            snapshot = await asyncio.wait_for(
                driver.recover_persisted_turn_snapshot(
                    turn_id,
                    timeout_seconds=command_timeout_seconds,
                ),
                timeout=transaction_timeout_seconds,
            )
        else:
            snapshot = await asyncio.wait_for(
                driver.recover_unpromoted_turn_snapshot(
                    turn_id,
                    expected_marker=token,
                    timeout_seconds=command_timeout_seconds,
                ),
                timeout=transaction_timeout_seconds,
            )
    except TimeoutError as exc:
        raise TimeoutError("LIVE_ACK_RECOVERY_TIMEOUT") from exc

    refreshed = driver.turn_binding(turn_id)
    if not isinstance(refreshed, dict):
        raise ValueError("LIVE_ACK_RECOVERY_BINDING_MISSING")
    refreshed_session = str(refreshed.get("session") or "").strip()
    refreshed_url = str(refreshed.get("conversation_url") or "").strip()
    if refreshed_session != session or not refreshed_url:
        raise ValueError("LIVE_ACK_RECOVERY_BINDING_MISSING")

    return {
        "status": "VERIFIED" if assistant_reply_matches_token(snapshot, token) else "ACK_NOT_VERIFIED",
        "turn_id": turn_id,
        "session": session,
        "conversation_url": refreshed_url,
    }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--transport", choices=("chrome-use", "relay-cdp"), default="chrome-use")
    parser.add_argument("--node-executable", default="node.exe")
    parser.add_argument("--relay-helper")
    parser.add_argument("--relay-state-path")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--recovery-command-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--recovery-transaction-timeout-seconds", type=float, default=12.0)
    return parser.parse_args()


def build_cli(args):
    transport = str(getattr(args, "transport", "chrome-use") or "chrome-use").strip().lower()
    if transport == "chrome-use":
        return ChromeUseCliV3(executable=args.executable)
    if transport == "relay-cdp":
        helper = str(getattr(args, "relay_helper", "") or "").strip()
        relay_state = str(getattr(args, "relay_state_path", "") or "").strip()
        if not helper or not relay_state:
            raise ValueError("LIVE_RELAY_CDP_CONFIG_MISSING")
        return RelayCdpCliV1(
            node_executable=str(getattr(args, "node_executable", "node.exe") or "node.exe"),
            helper_path=helper,
            state_path=relay_state,
        )
    raise ValueError("LIVE_TRANSPORT_INVALID")


async def run(args):
    cli = build_cli(args)
    driver = ChromeUseActorDriverV3(cli, args.state_path, timeout_seconds=30)
    prompt = f"Reply with exactly this one line and nothing else: {args.token}"
    first_snapshot = await driver.submit_prompt(
        prompt=prompt,
        turn_id=args.turn_id,
        actor_kind="WORKER",
        conversation_url=None,
    )
    binding = driver.turn_binding(args.turn_id)
    if not binding or not binding.get("conversation_url"):
        raise RuntimeError("LIVE_BINDING_MISSING")
    url = binding["conversation_url"]
    if not url.startswith("https://chatgpt.com/c/"):
        raise RuntimeError(f"LIVE_CANONICAL_URL_INVALID {url!r}")

    poll_seconds = float(getattr(args, "poll_seconds", 15.0))
    command_timeout = float(getattr(args, "recovery_command_timeout_seconds", 5.0))
    transaction_timeout = float(getattr(args, "recovery_transaction_timeout_seconds", 12.0))
    if poll_seconds <= 0:
        raise ValueError("LIVE_POLL_SECONDS_INVALID")

    deadline = time.monotonic() + args.timeout_seconds
    if assistant_reply_matches_token(first_snapshot, args.token):
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "conversation_url": url,
                    "turn_id": args.turn_id,
                    "token": args.token,
                    "ack_source": "submit_snapshot",
                },
                separators=(",", ":"),
            )
        )
        print("P0_LIVE_CHATGPT_PROBE=PASS")
        return 0

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(poll_seconds, remaining))
        try:
            recovered = await recover_persisted_live_ack(
                driver=driver,
                turn_id=args.turn_id,
                token=args.token,
                command_timeout_seconds=command_timeout,
                transaction_timeout_seconds=transaction_timeout,
            )
        except TimeoutError:
            continue
        if recovered["status"] == "VERIFIED":
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "conversation_url": recovered["conversation_url"],
                        "session": recovered["session"],
                        "turn_id": args.turn_id,
                        "token": args.token,
                        "ack_source": "persisted_session_recovery",
                    },
                    separators=(",", ":"),
                )
            )
            print("P0_LIVE_CHATGPT_PROBE=PASS")
            return 0

    raise TimeoutError("LIVE_RESPONSE_TOKEN_TIMEOUT")


def main():
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
