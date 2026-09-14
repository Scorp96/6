from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from actor_gui_backend_v3 import ActorGuiBackendV3
from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from chrome_use_cli_v3 import ChromeUseCliV3
from durable_actor_transport_v3 import DurableActorTransportV3


class CrashAfterRemoteSubmitBackend:
    def __init__(self, backend, evidence_path):
        self.backend = backend
        self.evidence_path = Path(evidence_path)

    async def submit(self, submission_id, **kwargs):
        handle = await self.backend.submit(submission_id, **kwargs)
        if self.evidence_path.exists():
            raise RuntimeError("CRASH_CANARY_REMOTE_SUBMIT_EVIDENCE_ALREADY_EXISTS")
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_path.write_text(
            json.dumps(
                {
                    "phase": "REMOTE_SUBMIT_CONFIRMED_BEFORE_LOCAL_HANDLE_PERSIST",
                    "submission_id": submission_id,
                    "handle": handle,
                    "submit_count": 1,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        os._exit(86)

    async def recover(self, *args, **kwargs):
        raise AssertionError("crash phase must not recover")

    async def poll(self, *args, **kwargs):
        raise AssertionError("crash phase must not poll")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    parser.add_argument("--driver-state", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--token", required=True)
    return parser.parse_args()


async def run(args):
    for raw in (args.driver_state, args.ledger, args.evidence):
        path = Path(raw)
        if path.exists():
            raise RuntimeError(f"CRASH_CANARY_FRESH_STATE_REQUIRED:{path}")
    cli = ChromeUseCliV3(executable=args.executable)
    driver = ChromeUseActorDriverV3(cli, args.driver_state, timeout_seconds=30)
    real_backend = ActorGuiBackendV3(driver)
    backend = CrashAfterRemoteSubmitBackend(real_backend, args.evidence)
    transport = DurableActorTransportV3(args.ledger, backend)
    prompt = (
        f"TURN_ID={args.turn_id}\n"
        "Reply with exactly one line and nothing else. Concatenate these four pieces with no spaces: "
        f"`SCORP_GUI_ACTOR_V3` + `::` + `{args.turn_id}` + `::` + `{args.token}`."
    )
    await transport.submit(
        prompt=prompt,
        turn_id=args.turn_id,
        actor_kind="MASTER",
        conversation_url=None,
        timeout_seconds=120,
    )
    raise AssertionError("CRASH_CANARY_EXPECTED_PROCESS_EXIT")


def main():
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
