"""Run one explicit Master A plan through the existing ChatGPT browser bridge.

The command is intentionally operator-gated. Without ``--send`` it only
returns ``SEND_REQUIRED`` and does not open Chrome, create SQLite state, or
touch a driver binding. With the gate, one structured plan is admitted to the
V4 controller, at most two Worker browser intents are active at a time, and
all local execution requests pass through the bounded execution and Git
worktree adapters.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import re
import sys
from collections.abc import Mapping
from typing import Any


BRIDGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_ROOT = BRIDGE_ROOT.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3  # noqa: E402
from chrome_use_cli_v3 import ChromeUseCliV3  # noqa: E402
from master_a_dynamic_v4.execution_adapter import LocalExecutionAdapter  # noqa: E402
from master_a_dynamic_v4.git_worktree import GitWorktreeManager  # noqa: E402
from master_a_dynamic_v4.master_controller import MasterAController  # noqa: E402
from master_a_dynamic_v4.models import sha256_json  # noqa: E402
from v4_bridge_gateway import V4BridgeGateway  # noqa: E402
from v4_auth import probe_chatgpt_auth  # noqa: E402
from v4_browser_engine import build_v4_browser_engine  # noqa: E402


DEFAULT_EXECUTABLE = r"C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe"


def _assistant_text(snapshot: str) -> str:
    text = str(snapshot or "")
    markers = ("#### ChatGPT 说：", "#### ChatGPT said:")
    positions = [(text.rfind(marker), marker) for marker in markers]
    start, marker = max(positions, key=lambda item: item[0])
    if start < 0:
        return ""
    return text[start + len(marker) :].strip()


def _response_matches_intent_assignment(value: Mapping[str, Any], intent_id: str) -> bool:
    """Require Worker responses to belong to the assignment encoded by the intent."""

    intent = str(intent_id or "").strip()
    prefix = "worker-intent-"
    if not intent.startswith(prefix):
        return True
    expected_assignment = intent[len(prefix) :].strip()
    if not expected_assignment:
        return False
    return str(value.get("assignment_id") or "").strip() == expected_assignment


def _response_matches_protocol(value: Mapping[str, Any], intent_id: str) -> bool:
    intent = str(intent_id or "").strip()
    if intent.startswith("master-reasoning-"):
        return (
            str(value.get("master_decision_version") or "") == "1"
            and str(value.get("intent_id") or "").strip() == intent
        )
    return (
        str(value.get("work_result_version") or "") == "1"
        and _response_matches_intent_assignment(value, intent)
    )


_MASTER_REASONING_BINDING_FIELDS = (
    "project_id",
    "intent_id",
    "master_epoch",
    "base_state_version",
    "operator_generation",
    "objective_generation",
    "input_snapshot_sha256",
)
_MASTER_REASONING_NUMERIC_BINDING_FIELDS = frozenset(
    {"master_epoch", "base_state_version", "operator_generation", "objective_generation"}
)


def _normalize_master_reasoning_response(
    value: Mapping[str, Any], intent_id: str
) -> dict[str, Any] | None:
    """Flatten a nested model binding without weakening durable validation.

    The live ChatGPT UI can return the supplied reasoning_binding object
    verbatim even though MASTER_DECISION/1 requires those identity fields at
    top level. Flatten only exact, non-conflicting fields. The coordinator
    still checks every field against SQLite before applying the action.
    """

    normalized = dict(value)
    intent = str(intent_id or "").strip()
    if not intent.startswith("master-reasoning-"):
        return normalized
    nested = normalized.get("reasoning_binding")
    if nested is None:
        return normalized
    if not isinstance(nested, Mapping):
        return None
    for key in _MASTER_REASONING_BINDING_FIELDS:
        if key not in nested:
            continue
        if key in normalized:
            actual = normalized[key]
            expected = nested[key]
            try:
                matched = (
                    int(actual) == int(expected)
                    if key in _MASTER_REASONING_NUMERIC_BINDING_FIELDS
                    else str(actual) == str(expected)
                )
            except (TypeError, ValueError):
                matched = False
            if not matched:
                return None
        else:
            normalized[key] = nested[key]
    return normalized


def parse_structured_response(snapshot: str, intent_id: str) -> dict[str, Any] | None:
    """Extract an intent-bound WORK_RESULT/1 or MASTER_DECISION/1 object."""

    text = _assistant_text(snapshot)
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"}:
            return None
        text = "\n".join(lines[1:-1]).strip()
    value = None
    if text.startswith("{") and text.endswith("}"):
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            value = None
    if isinstance(value, Mapping):
        normalized = _normalize_master_reasoning_response(value, intent_id)
        if normalized is not None and _response_matches_protocol(normalized, intent_id):
            return normalized

    # The Windows Chrome Use read path can preserve the assistant response but
    # decode the localized ``#### ChatGPT 说：`` marker as mojibake and append
    # a provider footer after the JSON.  Reconciliation must still consume the
    # already captured object; it must never resubmit merely because the
    # display marker or trailing page text is not canonical.  Restrict the
    # fallback to a JSON object appearing after an assistant marker and take
    # the last such object so the user's assignment prompt cannot be treated
    # as the Worker result.
    marker_position = str(snapshot or "").rfind("#### ChatGPT")
    if marker_position < 0:
        return None
    tail = str(snapshot or "")[marker_position:]
    decoder = json.JSONDecoder()
    # JSON object key order is not part of WORK_RESULT/1 or
    # MASTER_DECISION/1 identity.  The model may legally emit
    # acceptance_coverage (or any other field) before the version field.
    # Enumerate object starts after the assistant marker and let the existing
    # strict protocol/assignment validator decide which decoded mapping is the
    # authoritative response.  This stays fail-closed without depending on
    # presentation order.
    starts = list(re.finditer(r'\{\s*"', tail))
    for match in reversed(starts):
        try:
            candidate, _ = decoder.raw_decode(tail[match.start() :])
        except (TypeError, ValueError):
            continue
        if isinstance(candidate, Mapping):
            normalized = _normalize_master_reasoning_response(candidate, intent_id)
            if normalized is not None and _response_matches_protocol(
                normalized, intent_id
            ):
                return normalized
    return None


def _load_plan(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("PLAN_JSON_INVALID") from exc
    if not isinstance(value, Mapping):
        raise RuntimeError("PLAN_JSON_NOT_OBJECT")
    for key in ("root_contract", "acceptance_contract", "plan"):
        if not isinstance(value.get(key), Mapping) or not value[key]:
            raise RuntimeError(f"PLAN_{key.upper()}_MISSING")
    return {
        "root_contract": dict(value["root_contract"]),
        "acceptance_contract": dict(value["acceptance_contract"]),
        "plan": dict(value["plan"]),
    }


def validate_candidate_binding(
    manifest_path: str | pathlib.Path | None,
    *,
    candidate_commit: str,
    manifest_sha256: str,
) -> dict[str, str]:
    """Require an exact candidate manifest before any browser side effect."""

    if manifest_path is None:
        raise RuntimeError("CANDIDATE_BINDING_REQUIRED")
    path = pathlib.Path(manifest_path).resolve()
    if not path.is_file():
        raise RuntimeError("CANDIDATE_MANIFEST_MISSING")
    commit = str(candidate_commit or "").strip().lower()
    digest = str(manifest_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("CANDIDATE_COMMIT_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError("CANDIDATE_MANIFEST_HASH_INVALID")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("CANDIDATE_MANIFEST_INVALID") from exc
    if not isinstance(manifest, Mapping):
        raise RuntimeError("CANDIDATE_MANIFEST_INVALID")
    if str(manifest.get("candidate_commit") or "").strip().lower() != commit:
        raise RuntimeError("CANDIDATE_COMMIT_MISMATCH")
    if str(manifest.get("manifest_sha256") or "").strip().lower() != digest:
        raise RuntimeError("CANDIDATE_MANIFEST_HASH_MISMATCH")
    try:
        core = {key: manifest[key] for key in ("format", "candidate_commit", "source_tree", "files")}
    except KeyError as exc:
        raise RuntimeError("CANDIDATE_MANIFEST_INVALID") from exc
    if sha256_json(core) != digest:
        raise RuntimeError("CANDIDATE_MANIFEST_CANONICAL_HASH_MISMATCH")
    return {
        "candidate_commit": commit,
        "manifest_sha256": digest,
        "manifest_path": str(path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one gated SCORP V4 Master A plan")
    parser.add_argument("--send", action="store_true", help="required before opening ChatGPT or creating state")
    parser.add_argument("--plan-json", required=True, type=pathlib.Path)
    parser.add_argument("--database-path", required=True, type=pathlib.Path)
    parser.add_argument("--driver-state-path", required=True, type=pathlib.Path)
    parser.add_argument("--allowed-root", required=True, type=pathlib.Path)
    parser.add_argument("--executable", default=DEFAULT_EXECUTABLE)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--project-id", default="scorp-v4-master-runtime")
    parser.add_argument("--session-id", default="master-a-runtime")
    parser.add_argument("--max-cycles", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--allowed-module",
        action="append",
        default=["master_a_dynamic_v4.csv_workload.cli"],
        help="repeat for each local Python module a Worker may request",
    )
    parser.add_argument("--candidate-commit", default="")
    parser.add_argument("--artifact-manifest", type=pathlib.Path)
    parser.add_argument("--candidate-manifest", type=pathlib.Path)
    parser.add_argument("--manifest-sha256", default="")
    parser.add_argument("--evidence-path", type=pathlib.Path)
    return parser


def _prompt_task_context(value: Mapping[str, Any]) -> dict[str, Any]:
    context = dict(value or {})
    template = context.get("execution_request_template")
    if isinstance(template, Mapping):
        normalized = dict(template)
        backslash = chr(92)
        working_directory = normalized.get("working_directory")
        if isinstance(working_directory, str):
            normalized["working_directory"] = working_directory.replace(backslash, "/")
        resource_paths = normalized.get("resource_paths")
        if isinstance(resource_paths, (list, tuple)):
            normalized["resource_paths"] = [
                item.replace(backslash, "/") if isinstance(item, str) else item
                for item in resource_paths
            ]
        context["execution_request_template"] = normalized
    return context


def _worker_prompt(claim) -> str:
    assignment = {
        "project_id": claim.project_id,
        "assignment_id": claim.assignment_id,
        "task_id": claim.task_id,
        "worker_id": claim.worker_id,
        "slot_id": claim.slot_id,
        "master_epoch": claim.master_epoch,
        "base_state_version": claim.base_state_version,
        "objective_sha256": claim.objective_sha256,
        "resource_scope": list(claim.resource_scope),
        "access_mode": claim.access_mode,
    }
    task_context = _prompt_task_context(getattr(claim, "task_context", {}) or {})
    execution_template = (
        task_context.get("execution_request_template")
        if isinstance(task_context, Mapping)
        else None
    )
    execution_required = isinstance(execution_template, Mapping) and bool(execution_template)
    execution_instruction = (
        "For COMPLETE, copy execution_request exactly from "
        "task_context.execution_request_template; do not invent or broaden it."
        if execution_required
        else "This assignment does not authorize LocalExecution. Omit execution_request "
        "entirely; analysis/audit evidence is the deliverable."
    )
    evidence_example = (
        {"kind": "execution_request", "claim": "assigned request supplied"}
        if execution_required
        else {"kind": "source_audit", "claim": "bounded evidence supports the finding"}
    )
    return json.dumps(
        {
            "protocol": "SCORP V4 WORK_RESULT/1",
            "role": "dynamic Worker",
            "assignment": assignment,
            "task_context": task_context,
            "instructions": [
                "Only work inside the assignment resource_scope.",
                execution_instruction,
                "Return exactly one JSON object with work_result_version=1 and no explanatory prose.",
                "Use valid JSON string escaping; prefer forward-slash paths when a path is needed.",
                "For COMPLETE results, evidence must be an array of JSON objects with exactly kind and claim keys; do not use prose evidence strings.",
                "Do not restate execution_request.args, execution_request.resource_paths, arrays, objects, or other JSON syntax inside any free-text string field.",
                "Set status=COMPLETE only after non-empty scope_completed, evidence, and acceptance_coverage are supplied.",
                "Copy candidate_commit exactly from task_context into the result; include any 64-hex result_sha256 placeholder. The runtime recomputes result_sha256 from the captured normalized result and any authorized execution receipt.",
            ],
            "result_requirements": {
                "complete_requires_nonempty": [
                    "scope_completed",
                    "evidence",
                    "acceptance_coverage",
                ],
                "execution_request_required_for_complete": execution_required,
                "execution_request_forbidden_without_template": not execution_required,
                "candidate_commit_required": True,
                "result_sha256_required": True,
                "result_sha256": "64-hex placeholder accepted; runtime recomputes canonical content hash",
                "json_safety": {
                    "evidence_item_type": "object",
                    "evidence_required_keys": ["kind", "claim"],
                    "evidence_example": evidence_example,
                    "forbid_execution_request_restatement_in_strings": True,
                    "forbid_json_syntax_inside_free_text_strings": True,
                },
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

def run_runtime(args: argparse.Namespace) -> int:
    if not args.send:
        print(json.dumps({"status": "SEND_REQUIRED", "reason": "pass --send only after reviewing the plan and paths"}))
        return 2
    candidate_binding = validate_candidate_binding(
        args.candidate_manifest,
        candidate_commit=args.candidate_commit,
        manifest_sha256=args.manifest_sha256,
    )
    plan = _load_plan(args.plan_json)
    executable = pathlib.Path(args.executable)
    if not executable.is_file():
        raise RuntimeError("CHROME_USE_EXECUTABLE_MISSING")
    allowed_root = pathlib.Path(args.allowed_root).resolve(strict=True)
    database_path = pathlib.Path(args.database_path).resolve()
    driver_state_path = pathlib.Path(args.driver_state_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    driver_state_path.parent.mkdir(parents=True, exist_ok=True)

    cli = ChromeUseCliV3(executable=str(executable))
    driver = ChromeUseActorDriverV3(cli, driver_state_path, timeout_seconds=min(args.timeout_seconds, 120))

    async def auth_probe(channel: str):
        session = "scorp-v4-master-auth-" + hashlib.sha256(str(args.project_id).encode()).hexdigest()[:12]
        try:
            await cli.run_json(session, "open", "https://chatgpt.com/", timeout_seconds=30)
            return await probe_chatgpt_auth(cli, driver, session, channel)
        except Exception as exc:
            return {"status": "AUTH_PROBE_FAILED", "channel": channel, "error": type(exc).__name__}

    engine = build_v4_browser_engine(
        driver,
        auth_probe=auth_probe,
        response_parser=parse_structured_response,
        timeout_seconds=args.timeout_seconds,
    )
    gateway = V4BridgeGateway(
        database_path,
        str(args.project_id),
        [allowed_root],
        engine,
        master_ttl_seconds=max(60, min(args.timeout_seconds * 2, 3600)),
    )
    try:
        controller = MasterAController(
            gateway,
            str(args.session_id),
            execution_adapter=LocalExecutionAdapter(
                [allowed_root],
                python_executable=args.python_executable,
                pythonpath=AGENT_ROOT,
                allowed_modules=args.allowed_module,
            ),
            git_worktree_manager=GitWorktreeManager([allowed_root]),
        )
        started = controller.start(plan["root_contract"], plan["acceptance_contract"])
        admitted = controller.apply_plan(plan["plan"])
        history = controller.run_cycles(_worker_prompt, max_cycles=args.max_cycles)
        summary: dict[str, Any] = {
            "status": history[-1].status if history else "NOT_RUN",
            "project_id": str(args.project_id),
            "master": started,
            "plan": admitted,
            "cycles": [dataclasses.asdict(item) for item in history],
            "browser_io": "ATTEMPTED",
            "reasoning_model": "GPT-5.6 Sol",
            "worker_capacity": 2,
            "candidate_binding": candidate_binding,
        }
        if args.candidate_commit:
            artifacts: dict[str, str] = {}
            if args.artifact_manifest:
                artifacts = json.loads(args.artifact_manifest.read_text(encoding="utf-8"))
            decision = controller.completion(candidate_commit=args.candidate_commit, artifact_hashes=artifacts)
            summary["completion"] = {"status": decision.status.value, "blockers": list(decision.blockers)}
        if args.evidence_path:
            args.evidence_path.parent.mkdir(parents=True, exist_ok=True)
            args.evidence_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["status"] in {"IDLE", "TERMINAL"} else 2
    finally:
        gateway.close()


def main(argv: list[str] | None = None) -> int:
    return run_runtime(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
