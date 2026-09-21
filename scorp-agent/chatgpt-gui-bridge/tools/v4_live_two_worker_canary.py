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
    "worker-slot-1": "SCORPV4CANARYACK1",
    "worker-slot-2": "SCORPV4CANARYACK2",
}


def build_serialized_auth_probe(cli, driver, session: str):
    """Serialize read-only auth probes that share one Chrome Use session.

    Worker submissions remain concurrent, but a shared diagnostic session must
    not receive overlapping ``open``/``read`` commands. Chrome Use treats a
    session as a single actor, so concurrent probes can otherwise leave one
    intent before its browser turn is even bound.
    """

    probe_lock = asyncio.Lock()
    authenticated_cache: dict[str, object] | None = None

    async def auth_probe(channel: str):
        nonlocal authenticated_cache
        async with probe_lock:
            if authenticated_cache is not None:
                cached = dict(authenticated_cache)
                cached["channel"] = channel
                return cached
            await cli.run_json(session, "open", "https://chatgpt.com/", timeout_seconds=30)
            result = await probe_chatgpt_auth(cli, driver, session, channel)
            if isinstance(result, Mapping) and result.get("status") == "AUTHENTICATED":
                authenticated_cache = dict(result)
            return result

    return auth_probe


async def ensure_auth_preflight(auth_probe, *, timeout_seconds: float = 60.0):
    """Prove the shared browser session before creating any Worker intents."""

    try:
        result = await asyncio.wait_for(
            auth_probe("canary-preflight"), timeout=max(1.0, float(timeout_seconds))
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError("AUTH_PRECHECK_TIMEOUT") from exc
    if not isinstance(result, Mapping):
        raise RuntimeError("AUTH_PRECHECK_INVALID")
    status = str(result.get("status") or "UNKNOWN_AUTH_STATE")
    if status != "AUTHENTICATED":
        raise RuntimeError(f"AUTH_PRECHECK_BLOCKED:{status}")
    return dict(result)


def failure_evidence(
    *,
    project_id: str,
    error: Exception,
    intents: list[Mapping[str, object]],
    worker_count: int = 2,
) -> dict[str, object]:
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
        "format": (
            "scorp-v4-live-worker-canary-failure/1"
            if int(worker_count) == 1
            else "scorp-v4-two-worker-live-canary-failure/1"
        ),
        "project_id": str(project_id),
        "worker_count": int(worker_count),
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
    expected_marker: str | None = None,
    recovery_timeout_seconds: int = 5,
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
            if not bound_url and expected_marker:
                recover_unpromoted = getattr(
                    driver, "recover_unpromoted_turn_snapshot", None
                )
                if callable(recover_unpromoted):
                    try:
                        await recover_unpromoted(
                            intent_id,
                            expected_marker,
                            timeout_seconds=max(1, int(recovery_timeout_seconds)),
                        )
                    except (TimeoutError, ValueError):
                        # Read-only recovery is best-effort. Missing URL or
                        # marker stays ambiguous and must never resubmit.
                        pass
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


def _assistant_text(snapshot: str) -> str:
    text = str(snapshot or "")
    markers = ("#### ChatGPT 说：", "#### ChatGPT said:")
    positions = [(text.rfind(marker), marker) for marker in markers]
    start, marker = max(positions, key=lambda item: item[0])
    if start < 0:
        # The Windows CLI can decode the Chinese read marker with the wrong
        # console code page even though the assistant payload itself is valid
        # UTF-8/JSON.  Recover only a final object carrying the Worker result
        # discriminator; the caller still validates every identity field and
        # marker before admitting it.
        decoder = json.JSONDecoder()
        for index in range(len(text) - 1, -1, -1):
            if text[index] != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except (TypeError, ValueError):
                continue
            if isinstance(value, Mapping) and "work_result_version" in value:
                return json.dumps(dict(value), ensure_ascii=False)
        return ""
    tail = text[start + len(marker) :].strip()
    # ``read`` appends a localized safety footer after the assistant message.
    # Extract exactly the first WORK_RESULT/1 JSON object so the footer cannot
    # turn an otherwise valid response into a false capture failure.
    first_object = tail.find("{")
    if first_object >= 0:
        try:
            value, _ = json.JSONDecoder().raw_decode(tail[first_object:])
        except (TypeError, ValueError):
            value = None
        if isinstance(value, Mapping) and "work_result_version" in value:
            return json.dumps(dict(value), ensure_ascii=False)
    return tail


def _parse_response(expected_by_intent: Mapping[str, Mapping[str, object]]):
    """Parse a canary WORK_RESULT/1 and bind every identity field.

    The live canary uses the same capture-boundary contract as production
    Worker turns. A fixed marker alone proves only that a model replied; it
    cannot satisfy V4's assignment identity fence. The parser therefore
    accepts one JSON object (optionally in a JSON code fence), checks the
    assignment fields, and requires the harmless marker to be present in the
    declared scope/evidence.
    """

    def parser(snapshot: str, intent_id: str):
        expected = expected_by_intent.get(str(intent_id))
        if not isinstance(expected, Mapping):
            return None
        text = _assistant_text(snapshot)
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"}:
                return None
            text = "\n".join(lines[1:-1]).strip()
        value: object
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, Mapping):
            return None
        if str(value.get("work_result_version") or "") != "1":
            return None
        for key in (
            "project_id",
            "assignment_id",
            "task_id",
            "worker_id",
            "objective_sha256",
        ):
            if str(value.get(key) or "") != str(expected.get(key) or ""):
                return None
        try:
            if int(value.get("base_state_version")) != int(expected.get("base_state_version")):
                return None
        except (TypeError, ValueError):
            return None
        marker = str(expected.get("marker") or "")
        if not marker or marker not in json.dumps(dict(value), ensure_ascii=False, sort_keys=True):
            return None
        return dict(value)

    return parser


def _canary_prompt(claim, marker: str) -> str:
    """Create a harmless prompt that exercises the real Worker result fence."""

    response = {
        "work_result_version": "1",
        "project_id": claim.project_id,
        "assignment_id": claim.assignment_id,
        "task_id": claim.task_id,
        "worker_id": claim.worker_id,
        "objective_sha256": claim.objective_sha256,
        "base_state_version": claim.base_state_version,
        "status": "COMPLETE",
        "scope_completed": [marker],
        "evidence": [{"kind": "live_canary", "claim": marker}],
        "acceptance_coverage": ["LIVE_WORKER_CANARY"],
    }
    return (
        f"SCORP_V4_WORKER_CANARY_{claim.slot_id[-1]}: Reply with exactly one JSON object "
        "matching the following structure and nothing else. This is a harmless "
        "connectivity test. Do not access files, run commands, or perform any "
        "other action. Keep every field unchanged.\n"
        + json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )

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
    parser.add_argument(
        "--worker-count",
        type=int,
        choices=(1, 2),
        default=2,
        help="bounded live gate size: run one Worker before the two-Worker gate",
    )
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
    worker_count = int(getattr(args, "worker_count", 2))
    if worker_count not in (1, 2):
        raise RuntimeError("WORKER_COUNT_INVALID")
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
    expected_by_intent: dict[str, dict[str, object]] = {}
    driver = ChromeUseActorDriverV3(cli, args.driver_state_path, timeout_seconds=45)
    auth_session = "scorp-v4-live-auth-" + hashlib.sha256(str(args.project_id).encode()).hexdigest()[:12]
    prepared_intent_ids: list[str] = []
    driver.register_session(auth_session, role="DIAGNOSTIC")

    auth_probe = build_serialized_auth_probe(cli, driver, auth_session)

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
        await ensure_auth_preflight(
            auth_probe,
            timeout_seconds=min(60.0, float(args.timeout_seconds)),
        )
        gateway.ensure_contract(
            {"objective": "harmless live two-worker connectivity canary", "canary": True},
            {"required": ["LIVE_WORKER_CANARY"]},
        )
        master = gateway.start_master_session("master-live-canary")
        task_specs = [
            {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [args.allowed_root / "canary-t1.txt"], "dependencies": []},
            {"task_id": "T2", "objective_sha256": "2" * 64, "resource_scope": [args.allowed_root / "canary-t2.txt"], "dependencies": []},
        ][:worker_count]
        gateway.enqueue_graph(task_specs)
        claims = gateway.claim_workers(master_epoch=int(master["master_epoch"]), limit=worker_count)
        if len(claims) != worker_count:
            raise RuntimeError(f"EXPECTED_{worker_count}_CLAIMS:{len(claims)}")
        prepared = []
        for claim in claims:
            marker = MARKERS.get(claim.slot_id)
            if marker is None:
                raise RuntimeError("WORKER_SLOT_NOT_CANARY_BOUND")
            prompt = _canary_prompt(claim, marker)
            intent = gateway.prepare_worker_intent(claim, prompt, metadata={"canary_marker": marker})
            expected_by_intent[str(intent["intent_id"])] = {
                "marker": marker,
                "project_id": claim.project_id,
                "assignment_id": claim.assignment_id,
                "task_id": claim.task_id,
                "worker_id": claim.worker_id,
                "objective_sha256": claim.objective_sha256,
                "base_state_version": claim.base_state_version,
            }
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
                    expected_marker=marker,
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
            "format": (
                "scorp-v4-live-worker-canary/1"
                if worker_count == 1
                else "scorp-v4-two-worker-live-canary/1"
            ),
            "repository": "Scorp96/6",
            "candidate_code_commit": candidate_binding["candidate_commit"],
            "candidate_manifest_path": candidate_binding["manifest_path"],
            "candidate_manifest_sha256": candidate_binding["manifest_sha256"],
            "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "transport": "ChromeUseActorDriverV3 -> V4 BrowserAdapter -> V4BridgeGateway",
            "project_id": str(args.project_id),
            "worker_count": worker_count,
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
        receipt = failure_evidence(
            project_id=str(args.project_id),
            error=exc,
            intents=pending,
            worker_count=worker_count,
        )
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
