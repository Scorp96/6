"""Run the real two-Worker V4 browser canary with an explicit send gate.

The default command is read-only and refuses to send. ``--send-canary`` is a
deliberate operator action; prompts are fixed harmless marker requests and no
repository or customer data is read.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
from collections.abc import Mapping


BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3  # noqa: E402
from chrome_use_cli_v3 import ChromeUseCliV3  # noqa: E402
from v4_bridge_gateway import V4BridgeGateway  # noqa: E402
from v4_auth import classify_chatgpt_snapshot  # noqa: E402
from v4_browser_engine import build_v4_browser_engine  # noqa: E402


DEFAULT_EXECUTABLE = r"C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe"
MARKERS = {
    "worker-slot-1": "SCORP_V4_WORKER_CANARY_ACK_1",
    "worker-slot-2": "SCORP_V4_WORKER_CANARY_ACK_2",
}


def failure_evidence(*, project_id: str, error: Exception, intents: list[Mapping[str, object]]) -> dict[str, object]:
    """Build a fail-closed receipt after a live canary exception.

    The receipt records durable intent states without replaying or changing
    them. In particular, MAY_HAVE_SUBMITTED remains an operator reconcile
    blocker rather than being converted into a retry.
    """
    safe_intents = []
    for raw in intents:
        safe_intents.append(
            {
                key: raw.get(key)
                for key in ("intent_id", "state", "conversation_url", "remote_identity", "ambiguity_reason")
                if key in raw
            }
        )
    message = str(error)
    return {
        "format": "scorp-v4-two-worker-live-canary-failure/1",
        "project_id": str(project_id),
        "result": "BLOCKED",
        "reason": "LIVE_CANARY_EXCEPTION_FAIL_CLOSED",
        "error_type": type(error).__name__,
        "error_message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "retry_count": 0,
        "intents": safe_intents,
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }


async def dispatch_intents_concurrently(gateway: V4BridgeGateway, intent_ids: list[str]):
    """Submit distinct durable intents without letting one browser stall block another."""

    return list(
        await asyncio.gather(
            *(asyncio.to_thread(gateway.submit_intent, str(intent_id)) for intent_id in intent_ids),
            return_exceptions=True,
        )
    )


async def reconcile_intent_until_terminal(
    gateway: V4BridgeGateway,
    driver: ChromeUseActorDriverV3,
    intent: Mapping[str, object],
    initial_result,
    *,
    max_attempts: int = 8,
    sleep_seconds: float = 4.0,
):
    """Reconcile one Worker independently so another Worker cannot be skipped."""

    intent_id = str(intent["intent_id"])
    result = initial_result
    for attempt in range(max(1, int(max_attempts))):
        if isinstance(result, Mapping) and result.get("state") == "RESPONSE_CAPTURED":
            return dict(result)
        if attempt + 1 >= max(1, int(max_attempts)):
            break
        if sleep_seconds:
            await asyncio.sleep(float(sleep_seconds))
        current = await asyncio.to_thread(gateway.store.get_intent, intent_id)
        if (
            current.get("state") in {"MAY_HAVE_SUBMITTED", "BLOCKED_AMBIGUOUS"}
            and not current.get("conversation_url")
        ):
            bound_url = _driver_bound_url(driver, intent_id)
            if bound_url:
                remote = hashlib.sha256(
                    f"{intent_id}|{bound_url}".encode("utf-8")
                ).hexdigest()
                gateway.store.confirm_submitted(
                    intent_id,
                    conversation_url=bound_url,
                    remote_identity=remote,
                    observation={
                        "source": "driver_binding_read_only_reconcile",
                        "conversation_url": bound_url,
                    },
                )
        result = await asyncio.to_thread(gateway.adapter.reconcile, intent_id)
    return dict(result) if isinstance(result, Mapping) else result


def _reply_matches(snapshot: str, expected: str) -> bool:
    text = str(snapshot or "")
    positions = [(text.rfind(marker), marker) for marker in ("#### ChatGPT 说：", "#### ChatGPT said:")]
    start, marker = max(positions, key=lambda item: item[0])
    if start < 0:
        return False
    for line in text[start + len(marker) :].splitlines():
        value = line.strip()
        if value:
            return value == expected
    return False


def _parse_response(expected_by_intent: Mapping[str, str]):
    def parser(snapshot: str, intent_id: str):
        expected = expected_by_intent.get(str(intent_id))
        if expected and _reply_matches(snapshot, expected):
            return {"kind": "LIVE_WORKER_CANARY_ACK", "marker": expected}
        return None

    return parser


def _driver_bound_url(driver: ChromeUseActorDriverV3, intent_id: str) -> str | None:
    binding = driver.turn_binding(intent_id)
    value = str((binding or {}).get("conversation_url") or "").strip()
    if re.fullmatch(r"https://chatgpt\.com/c/[A-Za-z0-9-]+", value):
        return value
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SCORP V4 real two-Worker browser canary")
    parser.add_argument("--send-canary", action="store_true", help="required to send the two harmless prompts")
    parser.add_argument("--executable", default=DEFAULT_EXECUTABLE)
    parser.add_argument("--database-path", required=True, type=pathlib.Path)
    parser.add_argument("--driver-state-path", required=True, type=pathlib.Path)
    parser.add_argument("--allowed-root", required=True, type=pathlib.Path)
    parser.add_argument("--project-id", default="scorp-v4-live-two-worker-canary")
    parser.add_argument("--evidence-path", required=True, type=pathlib.Path)
    return parser


async def run_canary(args: argparse.Namespace) -> int:
    if not args.send_canary:
        print(json.dumps({"status": "SEND_REQUIRED", "reason": "pass --send-canary only after reviewing the fixed harmless prompts"}))
        return 2
    executable = pathlib.Path(args.executable)
    if not executable.is_file():
        raise RuntimeError("CHROME_USE_EXECUTABLE_MISSING")
    args.database_path.parent.mkdir(parents=True, exist_ok=True)
    args.driver_state_path.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_path.parent.mkdir(parents=True, exist_ok=True)

    cli = ChromeUseCliV3(executable=str(executable))
    expected_by_intent: dict[str, str] = {}
    driver = ChromeUseActorDriverV3(cli, args.driver_state_path, timeout_seconds=45)

    async def auth_probe(channel: str):
        session = "scorp-v4-live-auth-" + hashlib.sha256(str(args.project_id).encode()).hexdigest()[:12]
        await cli.run_json(session, "open", "https://chatgpt.com/", timeout_seconds=30)
        page = await cli.run_json(session, "read", timeout_seconds=30)
        text = json.dumps(page, ensure_ascii=False)
        return classify_chatgpt_snapshot(text, channel)

    engine = build_v4_browser_engine(
        driver,
        auth_probe=auth_probe,
        response_parser=_parse_response(expected_by_intent),
        timeout_seconds=75,
    )
    gateway = V4BridgeGateway(
        args.database_path,
        str(args.project_id),
        [args.allowed_root],
        engine,
        master_ttl_seconds=300,
    )
    records = []
    try:
        gateway.ensure_contract(
            {"objective": "harmless live two-worker connectivity canary", "canary": True},
            {"required": ["LIVE_WORKER_CANARY"]},
        )
        master = gateway.start_master_session("master-live-canary")
        gateway.enqueue_graph([
            {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [args.allowed_root / "canary-t1.txt"], "dependencies": []},
            {"task_id": "T2", "objective_sha256": "2" * 64, "resource_scope": [args.allowed_root / "canary-t2.txt"], "dependencies": []},
        ])
        claims = gateway.claim_workers(master_epoch=int(master["master_epoch"]), limit=2)
        if len(claims) != 2:
            raise RuntimeError(f"EXPECTED_TWO_CLAIMS:{len(claims)}")
        prepared = []
        for claim in claims:
            marker = MARKERS.get(claim.slot_id)
            if marker is None:
                raise RuntimeError("WORKER_SLOT_NOT_CANARY_BOUND")
            prompt = (
                f"SCORP_V4_WORKER_CANARY_{claim.slot_id[-1]}: Reply with exactly {marker} and nothing else. "
                "This is a harmless connectivity test. Do not access files, run commands, or perform any other action."
            )
            intent = gateway.prepare_worker_intent(claim, prompt, metadata={"canary_marker": marker})
            expected_by_intent[str(intent["intent_id"])] = marker
            prepared.append((claim, marker, intent))
        submitted = await dispatch_intents_concurrently(
            gateway, [str(intent["intent_id"]) for _, _, intent in prepared]
        )
        reconciled = await asyncio.gather(
            *(
                reconcile_intent_until_terminal(
                    gateway,
                    driver,
                    intent,
                    submitted_result,
                )
                if not isinstance(submitted_result, Exception)
                else asyncio.sleep(0, result=submitted_result)
                for (_, _, intent), submitted_result in zip(prepared, submitted, strict=True)
            ),
            return_exceptions=True,
        )
        failures = []
        for (_, marker, intent), result in zip(prepared, reconciled, strict=True):
            if isinstance(result, Exception):
                failures.append(f"WORKER_RESPONSE_EXCEPTION:{intent['intent_id']}:{type(result).__name__}")
                continue
            if not isinstance(result, Mapping) or result.get("state") != "RESPONSE_CAPTURED":
                failures.append(
                    f"WORKER_RESPONSE_NOT_CAPTURED:{intent['intent_id']}:{result.get('state') if isinstance(result, Mapping) else type(result).__name__}:{result.get('ambiguity_reason') if isinstance(result, Mapping) else ''}"
                )
                continue
            records.append(
                {
                    "intent_id": intent["intent_id"],
                    "channel": intent["channel"],
                    "conversation_url": result.get("conversation_url"),
                    "state": result.get("state"),
                    "response_sha256": result.get("response_sha256"),
                    "marker": marker,
                }
            )
        if failures:
            raise RuntimeError(";".join(failures))
        evidence = {
            "format": "scorp-v4-two-worker-live-canary/1",
            "repository": "Scorp96/6",
            "candidate_code_commit": "runtime-provided",
            "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "transport": "ChromeUseActorDriverV3 -> V4 BrowserAdapter -> V4BridgeGateway",
            "project_id": str(args.project_id),
            "master_epoch": int(master["master_epoch"]),
            "worker_count": len(records),
            "submit_actions": len(records),
            "response_messages": len(records),
            "duplicate_submits": 0,
            "records": records,
            "result": "PASS_REAL_BROWSER_TWO_WORKER_SUBMIT_AND_RECONCILIATION",
            "limitations": [
                "Prompts are harmless fixed-marker connectivity checks and do not perform repository work.",
                "This does not authorize production cutover or an unattended soak.",
            ],
        }
        args.evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        try:
            pending = gateway.store.pending_intents()
        except Exception:
            pending = []
        receipt = failure_evidence(project_id=str(args.project_id), error=exc, intents=pending)
        args.evidence_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
        return 2
    finally:
        gateway.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run_canary(args))


if __name__ == "__main__":
    raise SystemExit(main())
