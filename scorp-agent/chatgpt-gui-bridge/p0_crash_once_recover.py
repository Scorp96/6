from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

from actor_gui_backend_v3 import ActorGuiBackendV3
from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from chrome_use_cli_v3 import ChromeUseCliV3
from durable_actor_transport_v3 import DurableActorTransportV3


class NoResubmitBackend:
    def __init__(self, backend):
        self.backend = backend
        self.submit_calls = 0
        self.recover_calls = 0
        self.poll_calls = 0

    async def submit(self, *args, **kwargs):
        self.submit_calls += 1
        raise AssertionError("CRASH_RECOVERY_RESUBMIT_FORBIDDEN")

    async def recover(self, *args, **kwargs):
        self.recover_calls += 1
        return await self.backend.recover(*args, **kwargs)

    async def poll(self, *args, **kwargs):
        self.poll_calls += 1
        return await self.backend.poll(*args, **kwargs)


def _expected_response(token):
    token = str(token or "").strip()
    if not token:
        raise ValueError("CRASH_RECOVERY_TOKEN_MISSING")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("CRASH_RECOVERY_TOKEN_INVALID") from exc
    if not isinstance(value, dict) or str(value.get("kind") or "").upper() != "WAIT":
        raise ValueError("CRASH_RECOVERY_TOKEN_INVALID")
    value = dict(value)
    value["kind"] = "WAIT"
    return value


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--driver-state", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--evidence")
    parser.add_argument("--expected-conversation-url", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args()


async def run(args):
    expected_response = _expected_response(args.token)
    ledger_path = Path(args.ledger)
    if not ledger_path.is_file():
        raise RuntimeError("CRASH_RECOVERY_DURABLE_LEDGER_MISSING")
    before_rows = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    before = before_rows.get(args.turn_id)
    if not isinstance(before, dict) or before.get("state") != "SUBMITTING":
        raise RuntimeError("CRASH_RECOVERY_EXPECTED_SUBMITTING")

    expected_url = str(args.expected_conversation_url or "").strip()
    if not expected_url.startswith("https://chatgpt.com/c/"):
        raise ValueError("CRASH_RECOVERY_EXPECTED_URL_INVALID")
    evidence = None
    if args.evidence:
        evidence_path = Path(args.evidence)
        if evidence_path.is_file():
            evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
            if evidence.get("submit_count") != 1:
                raise RuntimeError("CRASH_RECOVERY_SUBMIT_COUNT_INVALID")
            remote_handle = evidence.get("handle") or {}
            if remote_handle.get("conversation_url") != expected_url:
                raise RuntimeError("CRASH_RECOVERY_EVIDENCE_URL_CONFLICT")

    cli = ChromeUseCliV3(executable=args.executable)
    driver = ChromeUseActorDriverV3(cli, args.driver_state, timeout_seconds=10)
    before_binding = driver.turn_binding(args.turn_id)
    before_session = str((before_binding or {}).get("session") or "").strip()
    if not before_session:
        raise RuntimeError("CRASH_RECOVERY_SESSION_MISSING")
    backend = NoResubmitBackend(ActorGuiBackendV3(driver))
    transport = DurableActorTransportV3(args.ledger, backend)
    prompt = (
        f"TURN_ID={args.turn_id}\n"
        "Reply with exactly one line and nothing else. Concatenate these four pieces with no spaces: "
        f"`SCORP_GUI_ACTOR_V3` + `::` + `{args.turn_id}` + `::` + `{args.token}`."
    )

    recovered = await transport.submit(
        prompt=prompt,
        turn_id=args.turn_id,
        actor_kind="MASTER",
        conversation_url=None,
        timeout_seconds=120,
    )
    if recovered.get("status") != "RECOVERED":
        raise RuntimeError(f"CRASH_RECOVERY_NOT_RECOVERED:{recovered.get('status')}")
    if backend.submit_calls != 0 or backend.recover_calls != 1:
        raise RuntimeError("CRASH_RECOVERY_EXACTLY_ONCE_GUARD_FAILED")
    after_binding = driver.turn_binding(args.turn_id)
    if str((after_binding or {}).get("session") or "").strip() != before_session:
        raise RuntimeError("CRASH_RECOVERY_SESSION_CHANGED")
    if recovered.get("conversation_url") != expected_url:
        raise RuntimeError("CRASH_RECOVERY_CONVERSATION_CHANGED")

    deadline = time.monotonic() + float(args.timeout_seconds)
    last = None
    while time.monotonic() < deadline:
        last = await transport.poll(args.turn_id, timeout_seconds=10)
        if last.get("status") == "COMPLETED":
            break
        await asyncio.sleep(5)
    if not isinstance(last, dict) or last.get("status") != "COMPLETED":
        raise TimeoutError("CRASH_RECOVERY_RESPONSE_TIMEOUT")
    if last.get("response") != expected_response:
        raise RuntimeError(f"CRASH_RECOVERY_RESPONSE_INVALID:{last.get('response')!r}")

    after_rows = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    after = after_rows.get(args.turn_id)
    if not isinstance(after, dict) or after.get("state") != "COMPLETED":
        raise RuntimeError("CRASH_RECOVERY_LEDGER_NOT_COMPLETED")
    handle = after.get("handle") or {}
    if handle.get("conversation_url") != expected_url:
        raise RuntimeError("CRASH_RECOVERY_CONVERSATION_CHANGED")

    print(json.dumps({
        "status": "PASS",
        "turn_id": args.turn_id,
        "submission_id": after.get("submission_id"),
        "session": before_session,
        "conversation_url": expected_url,
        "submit_count": 1,
        "resubmit_count": backend.submit_calls,
        "recover_count": backend.recover_calls,
        "poll_count": backend.poll_calls,
        "ledger_state": after.get("state"),
        "response": last.get("response"),
        "remote_submit_evidence_file": evidence is not None,
    }, separators=(",", ":")))
    print("P0_CRASH_RECOVERY_EXACTLY_ONCE=PASS")
    return 0


def main():
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
