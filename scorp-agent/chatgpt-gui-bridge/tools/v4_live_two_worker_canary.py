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
from v4_auth import probe_chatgpt_auth  # noqa: E402
from v4_browser_engine import build_v4_browser_engine  # noqa: E402
from tools.v4_master_controller_runtime import validate_candidate_binding  # noqa: E402


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


async def cleanup_canary_lifecycle(
    driver: ChromeUseActorDriverV3,
    store,
    intent_ids: list[str],
    *,
    auth_session: str | None,
) -> dict[str, object]:
    """Stop only canary sessions whose side effects have a terminal proof.

    A captured response (or positive proof that nothing was submitted) makes
    the corresponding Worker turn safe to retire.  MAY_HAVE_SUBMITTED and
    BLOCKED_AMBIGUOUS turns are deliberately preserved for reconciliation and
    are never stopped here.  The dedicated auth-probe session is temporary and
    has no submit intent, so it is always retired and any cleanup failure is
    recorded rather than retried.
    """

    retired_turn_ids: list[str] = []
    preserved: list[dict[str, object]] = []
    for raw_intent_id in intent_ids:
        intent_id = str(raw_intent_id)
        try:
            current = store.get_intent(intent_id)
        except Exception as exc:
            preserved.append(
                {
                    "intent_id": intent_id,
                    "state": "UNKNOWN",
                    "reason": "INTENT_LOOKUP_FAILED",
                    "error_type": type(exc).__name__,
                }
            )
            continue
        state = str(current.get("state") or "UNKNOWN")
        if state not in {"RESPONSE_CAPTURED", "VERIFIED_NOT_SUBMITTED"}:
            preserved.append(
                {
                    "intent_id": intent_id,
                    "state": state,
                    "reason": "NON_TERMINAL_OR_AMBIGUOUS",
                }
            )
            continue
        binding = driver.turn_binding(intent_id)
        session = str((binding or {}).get("session") or "")
        if not session.startswith("scorp-p0-turn-"):
            preserved.append(
                {
                    "intent_id": intent_id,
                    "state": state,
                    "reason": "WORKER_SESSION_NOT_TEMPORARY",
                    "session": session or None,
                }
            )
            continue
        try:
            result = await driver.retire_turn(
                intent_id,
                reason="live canary terminal result",
                stop=True,
                allow_persistent=True,
            )
        except Exception as exc:
            preserved.append(
                {
                    "intent_id": intent_id,
                    "state": state,
                    "reason": "WORKER_CLEANUP_BLOCKED",
                    "error_type": type(exc).__name__,
                }
            )
            continue
        retired_turn_ids.append(intent_id)

    auth_result: dict[str, object] | None = None
    if auth_session:
        try:
            auth_result = dict(
                await driver.retire_session(
                    auth_session,
                    reason="live canary auth probe complete",
                    stop=True,
                )
            )
        except Exception as exc:
            auth_result = {
                "status": "CLEANUP_BLOCKED",
                "session": str(auth_session),
                "error_type": type(exc).__name__,
            }
    return {
        "retired_turn_ids": retired_turn_ids,
        "preserved": preserved,
        "auth_session": auth_result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SCORP V4 real two-Worker browser canary")
    parser.add_argument("--send-canary", action="store_true", help="required to send the two harmless prompts")
    parser.add_argument("--executable", default=DEFAULT_EXECUTABLE)
    parser.add_argument("--database-path", required=True, type=pathlib.Path)
    parser.add_argument("--driver-state-path", required=True, type=pathlib.Path)
    parser.add_argument("--allowed-root", required=True, type=pathlib.Path)
    parser.add_argument("--project-id", default="scorp-v4-live-two-worker-canary")
    parser.add_argument("--evidence-path", required=True, type=pathlib.Path)
    parser.add_argument("--candidate-commit", default="", help="exact 40-hex candidate commit")
    parser.add_argument("--candidate-manifest", type=pathlib.Path)
    parser.add_argument("--manifest-sha256", default="", help="exact 64-hex candidate manifest hash")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=360,
        help="bounded submit timeout; includes the five-minute rate-limit recovery window",
    )
    return parser


async def run_canary(args: argparse.Namespace) -> int:
    if not args.send_canary:
        print(json.dumps({"status": "SEND_REQUIRED", "reason": "pass --send-canary only after reviewing the fixed harmless prompts"}))
        return 2
    if not args.candidate_commit or args.candidate_manifest is None or not args.manifest_sha256:
        raise RuntimeError("CANDIDATE_BINDING_REQUIRED")
    candidate_binding = validate_candidate_binding(
        args.candidate_manifest,
        candidate_commit=args.candidate_commit,
        manifest_sha256=args.manifest_sha256,
    )
    executable = pathlib.Path(args.executable)
    if not executable.is_file():
        raise RuntimeError("CHROME_USE_EXECUTABLE_MISSING")
    args.database_path.parent.mkdir(parents=True, exist_ok=True)
    args.driver_state_path.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_path.parent.mkdir(parents=True, exist_ok=True)

    cli = ChromeUseCliV3(executable=str(executable))
    expected_by_intent: dict[str, str] = {}
    driver = ChromeUseActorDriverV3(cli, args.driver_state_path, timeout_seconds=45)
    auth_session = "scorp-v4-live-auth-" + hashlib.sha256(str(args.project_id).encode()).hexdigest()[:12]
    prepared_intent_ids: list[str] = []
    driver.register_session(auth_session, role="DIAGNOSTIC")

    async def auth_probe(channel: str):
        await cli.run_json(auth_session, "open", "https://chatgpt.com/", timeout_seconds=30)
        return await probe_chatgpt_auth(cli, driver, auth_session, channel)

    engine = build_v4_browser_engine(
        driver,
        auth_probe=auth_probe,
        response_parser=_parse_response(expected_by_intent),
        timeout_seconds=max(1, int(args.timeout_seconds)),
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
            prepared_intent_ids.append(str(intent["intent_id"]))
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
        lifecycle_cleanup = await cleanup_canary_lifecycle(
            driver,
            gateway.store,
            prepared_intent_ids,
            auth_session=auth_session,
        )
        evidence = {
            "format": "scorp-v4-two-worker-live-canary/1",
            "repository": "Scorp96/6",
            "candidate_code_commit": candidate_binding["candidate_commit"],
            "candidate_manifest_path": candidate_binding["manifest_path"],
            "candidate_manifest_sha256": candidate_binding["manifest_sha256"],
            "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "transport": "ChromeUseActorDriverV3 -> V4 BrowserAdapter -> V4BridgeGateway",
            "project_id": str(args.project_id),
            "master_epoch": int(master["master_epoch"]),
            "worker_count": len(records),
            "submit_actions": len(records),
            "response_messages": len(records),
            "duplicate_submits": 0,
            "records": records,
            "lifecycle_cleanup": lifecycle_cleanup,
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
        lifecycle_cleanup = await cleanup_canary_lifecycle(
            driver,
            gateway.store,
            prepared_intent_ids,
            auth_session=auth_session,
        )
        receipt = failure_evidence(project_id=str(args.project_id), error=exc, intents=pending)
        receipt.update(
            {
                "candidate_code_commit": candidate_binding["candidate_commit"],
                "candidate_manifest_path": candidate_binding["manifest_path"],
                "candidate_manifest_sha256": candidate_binding["manifest_sha256"],
            }
        )
        receipt["lifecycle_cleanup"] = lifecycle_cleanup
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
