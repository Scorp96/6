from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from actor_gui_backend_v3 import ActorGuiBackendV3
from continuation_watchdog_v3 import ContinuationWatchdog
from durable_actor_transport_v3 import DurableActorTransportV3
from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from master_worker_coordinator_v3 import MasterWorkerCoordinatorV3
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from project_bootstrap_v3 import ProjectBootstrapV3
from project_lifecycle_v3 import ProjectLifecycleV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3
from worker_event_pump_v3 import WorkerEventPumpV3
from worker_event_queue_v3 import WorkerEventQueueV3


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _json_files(path: Path):
    if not path.is_dir():
        return []
    return [_read_json(p) for p in sorted(path.glob("*.json"))]


def _closed_hwnds(transport: dict) -> int:
    user32 = ctypes.windll.user32
    count = 0
    for turn_id, row in transport.items():
        handle = (row or {}).get("handle") if isinstance(row, dict) else None
        hwnd = handle.get("window_handle") if isinstance(handle, dict) else None
        if type(hwnd) is not int or hwnd <= 0:
            raise RuntimeError("E2E_WINDOW_HANDLE_MISSING:" + str(turn_id))
        if user32.IsWindow(hwnd):
            raise RuntimeError("E2E_WINDOW_LEAK:" + str(turn_id))
        count += 1
    return count


def _assignment(task: str, scope: str):
    return {
        "objective_sha256": _sha_text(task),
        "task": task,
        "resource_scope": [scope],
        "success_criteria": "Return exactly the HANDOFF payload required by task.",
    }


def _contract():
    alpha_task = (
        'Return HANDOFF only. Top-level summary must be exactly "e2e-worker-alpha". '
        'Top-level evidence must be exactly ["alpha-proof"]. '
        'Top-level resource_scope must be exactly ["e2e/alpha"]. '
        'Top-level remaining and blockers must both be empty arrays.'
    )
    beta_task = (
        'Return HANDOFF only. Top-level summary must be exactly "e2e-worker-beta". '
        'Top-level evidence must be exactly ["beta-proof"]. '
        'Top-level resource_scope must be exactly ["e2e/beta"]. '
        'Top-level remaining and blockers must both be empty arrays.'
    )
    alpha = _assignment(alpha_task, "e2e/alpha")
    beta = _assignment(beta_task, "e2e/beta")

    next_after_dispatch = (
        "On CONTINUE_CORE_AFTER_DISPATCH or CONTINUE_CORE, return WAIT and dispatch no additional workers. "
        "On WORKER_HANDOFF, require worker_guard.verdict ALIGNED and validate the exact worker summary/evidence/resource_scope. "
        "Use payload.project_state.active_workers BEFORE the current event is committed: when its length is 2, this is the first worker event; "
        "return CONTINUE with state_patch.current_phase E2E_ONE_WORKER_FUSED, state_patch.evidence equal to a one-item array containing the current worker summary, "
        "and state_patch.next_exact_action equal to this same worker-event policy. When active_workers length is 1, this is the second worker event; "
        "return DRAIN with state_patch.current_phase E2E_HANDOFF_READY, state_patch.evidence containing both e2e-worker-alpha and e2e-worker-beta exactly once in any order, "
        "state_patch.tests [\"worker-fusion-guard-pass\"], and state_patch.next_exact_action exactly \"D must resume persistent A in the same canonical conversation; then A must TERMINAL COMPLETE.\""
    )

    root_objective = (
        "This is an unattended bounded production-style E2E canary. Do no unrelated work. Persistent Master identity is always A. "
        "On RESUME_MASTER inspect payload.project_state.current_phase. If it is BOOTSTRAPPED, return DISPATCH with exactly two assignments and no others. "
        "Assignment alpha must be exactly " + json.dumps(alpha, ensure_ascii=False, separators=(",", ":")) + ". "
        "Assignment beta must be exactly " + json.dumps(beta, ensure_ascii=False, separators=(",", ":")) + ". "
        "The DISPATCH state_patch must set current_phase to E2E_WORKERS_ACTIVE and next_exact_action to: " + next_after_dispatch + ". "
        + next_after_dispatch
        + " On a later RESUME_MASTER when payload.project_state.current_phase is E2E_HANDOFF_READY, verify previous_master_lease.status is DRAINED, "
        "verify project_state.active_workers is empty and project_state.evidence contains both worker summaries, then return TERMINAL with terminal_status COMPLETE. "
        "That TERMINAL state_patch must set current_phase E2E_COMPLETE, next_exact_action \"archive terminal canary\", "
        "evidence [\"e2e-worker-alpha\",\"e2e-worker-beta\",\"d-reactivated-same-a\"], tests [\"worker-fusion-guard-pass\",\"persistent-a-dynamic-workers-e2e-pass\"]. "
        "Never TERMINAL before the DRAIN and subsequent RESUME_MASTER."
    )
    acceptance = (
        "PASS only if one persistent Master identity A uses one canonical ChatGPT conversation across at least three Master turns; "
        "A dispatches exactly two disjoint ephemeral workers; both workers HANDOFF exact bounded evidence; both WorkerEvents are guard-ALIGNED and ACKED; "
        "A fuses the first event, DRAINs after the second, deterministic D creates a new internal A session with reason LEASE_ENDED, and that resumed A reuses the same canonical conversation and TERMINALs COMPLETE; "
        "there is no AMBIGUOUS/TIMED_OUT/stale inflight transport row, no second active Master lease, and the terminal project archives validly."
    )
    return root_objective, acceptance, alpha, beta


def _validate(root: Path, project_id: str, alpha: dict, beta: dict):
    state = ProjectStateStore(root / "project-state.json").load()
    if state.get("status") != "COMPLETE" or state.get("current_phase") != "E2E_COMPLETE":
        raise RuntimeError("E2E_TERMINAL_STATE_INVALID:" + json.dumps(state, ensure_ascii=True, sort_keys=True))
    if state.get("active_workers"):
        raise RuntimeError("E2E_ACTIVE_WORKERS_REMAIN")
    if set(state.get("evidence") or []) != {"e2e-worker-alpha", "e2e-worker-beta", "d-reactivated-same-a"}:
        raise RuntimeError("E2E_FINAL_EVIDENCE_INVALID")

    inbox = _json_files(root / "master-worker-v3-inbox")
    master_rows = [r for r in inbox if str(((r.get("turn") or {}).get("actor_kind") or "")).upper() == "MASTER"]
    worker_rows = [r for r in inbox if str(((r.get("turn") or {}).get("actor_kind") or "")).upper() == "WORKER"]
    if len(master_rows) < 5:
        raise RuntimeError("E2E_MASTER_TURN_COUNT_TOO_LOW:" + str(len(master_rows)))
    if len(worker_rows) != 2:
        raise RuntimeError("E2E_WORKER_TURN_COUNT_INVALID:" + str(len(worker_rows)))
    if any((r.get("turn") or {}).get("actor_id") != "A" for r in master_rows):
        raise RuntimeError("E2E_MASTER_IDENTITY_DRIFT")

    master_urls = [str(r.get("conversation_url") or "") for r in master_rows]
    if any(not u for u in master_urls) or len(set(master_urls)) != 1:
        raise RuntimeError("E2E_MASTER_CONVERSATION_NOT_PERSISTENT:" + json.dumps(master_urls))
    master_sessions = {str((r.get("turn") or {}).get("session_id") or "") for r in master_rows}
    if len(master_sessions) < 2:
        raise RuntimeError("E2E_DID_NOT_CREATE_SECOND_MASTER_SESSION")

    resume_rows = [r for r in master_rows if str((((r.get("turn") or {}).get("payload") or {}).get("event") or "")) == "RESUME_MASTER"]
    if len(resume_rows) != 2:
        raise RuntimeError("E2E_RESUME_COUNT_INVALID:" + str(len(resume_rows)))
    first_resume = resume_rows[0]
    second_resume = resume_rows[1]
    first_reason = str((((first_resume.get("turn") or {}).get("payload") or {}).get("reason") or ""))
    second_reason = str((((second_resume.get("turn") or {}).get("payload") or {}).get("reason") or ""))
    if first_reason != "NO_ACTIVE_MASTER" or second_reason != "LEASE_ENDED":
        raise RuntimeError("E2E_RESUME_REASONS_INVALID:" + first_reason + ":" + second_reason)
    if str((first_resume.get("response") or {}).get("kind") or "").upper() != "DISPATCH":
        raise RuntimeError("E2E_INITIAL_MASTER_NOT_DISPATCH")
    if str((second_resume.get("response") or {}).get("kind") or "").upper() != "TERMINAL":
        raise RuntimeError("E2E_RESUMED_MASTER_NOT_TERMINAL")

    dispatch_assignments = (first_resume.get("response") or {}).get("assignments") or []
    if len(dispatch_assignments) != 2:
        raise RuntimeError("E2E_ASSIGNMENT_COUNT_INVALID")
    expected = {
        alpha["objective_sha256"]: alpha,
        beta["objective_sha256"]: beta,
    }
    observed = {a.get("objective_sha256"): a for a in dispatch_assignments if isinstance(a, dict)}
    if observed != expected:
        raise RuntimeError("E2E_ASSIGNMENT_BINDING_INVALID")

    worker_expect = {
        alpha["objective_sha256"]: ("e2e-worker-alpha", ["alpha-proof"], ["e2e/alpha"]),
        beta["objective_sha256"]: ("e2e-worker-beta", ["beta-proof"], ["e2e/beta"]),
    }
    worker_sessions = set()
    worker_urls = set()
    for row in worker_rows:
        turn = row.get("turn") or {}
        objective = turn.get("objective_sha256")
        response = row.get("response") or {}
        exp = worker_expect.get(objective)
        if exp is None:
            raise RuntimeError("E2E_WORKER_OBJECTIVE_UNKNOWN")
        if str(response.get("kind") or "").upper() != "HANDOFF":
            raise RuntimeError("E2E_WORKER_NOT_HANDOFF")
        if response.get("summary") != exp[0] or response.get("evidence") != exp[1] or response.get("resource_scope") != exp[2]:
            raise RuntimeError("E2E_WORKER_PAYLOAD_INVALID:" + str(objective))
        worker_sessions.add(str(turn.get("session_id") or ""))
        worker_urls.add(str(row.get("conversation_url") or ""))
    if len(worker_sessions) != 2 or len(worker_urls) != 2 or master_urls[0] in worker_urls:
        raise RuntimeError("E2E_WORKER_SESSION_OR_CONVERSATION_INVALID")

    event_master_rows = [r for r in master_rows if str((((r.get("turn") or {}).get("payload") or {}).get("event") or "")) == "WORKER_HANDOFF"]
    if len(event_master_rows) != 2:
        raise RuntimeError("E2E_WORKER_EVENT_MASTER_COUNT_INVALID")
    kinds = []
    for row in event_master_rows:
        payload = (row.get("turn") or {}).get("payload") or {}
        if ((payload.get("worker_guard") or {}).get("verdict")) != "ALIGNED":
            raise RuntimeError("E2E_WORKER_GUARD_NOT_ALIGNED")
        kinds.append(str((row.get("response") or {}).get("kind") or "").upper())
    if sorted(kinds) != ["CONTINUE", "DRAIN"]:
        raise RuntimeError("E2E_MASTER_FUSION_KINDS_INVALID:" + json.dumps(kinds))

    events = _json_files(root / "worker-events-v3" / "events")
    if len(events) != 2 or any(e.get("state") != "ACKED" for e in events):
        raise RuntimeError("E2E_WORKER_EVENTS_NOT_ACKED")

    ledger = _read_json(root / "master-worker-v3-ledger.json")
    if not ledger or any((row or {}).get("state") != "ROUTED" for row in ledger.values()):
        raise RuntimeError("E2E_RELAY_LEDGER_NOT_FULLY_ROUTED")
    transport = _read_json(root / "actor-transport-v3.json")
    if len(transport) != len(ledger):
        raise RuntimeError("E2E_TRANSPORT_LEDGER_COUNT_MISMATCH")
    bad_transport = {k: (v or {}).get("state") for k, v in transport.items() if (v or {}).get("state") != "COMPLETED"}
    if bad_transport:
        raise RuntimeError("E2E_TRANSPORT_NOT_COMPLETED:" + json.dumps(bad_transport, sort_keys=True))

    lease = _read_json(root / "master-window-lease.json").get(project_id)
    if not isinstance(lease, dict) or lease.get("status") != "RELEASED":
        raise RuntimeError("E2E_FINAL_MASTER_LEASE_INVALID")
    second_session = str((second_resume.get("turn") or {}).get("session_id") or "")
    if lease.get("session_id") != second_session:
        raise RuntimeError("E2E_FINAL_MASTER_LEASE_SESSION_INVALID")

    sessions = SessionRegistryV3(root / "sessions-v3.json")
    binding = sessions.get_master_conversation(project_id)
    if not isinstance(binding, dict) or binding.get("conversation_url") != master_urls[0]:
        raise RuntimeError("E2E_MASTER_CONVERSATION_BINDING_INVALID")
    if int(binding.get("rotation_count") or 0) != 0:
        raise RuntimeError("E2E_MASTER_CONVERSATION_ROTATED")

    closed = _closed_hwnds(transport)
    return {
        "terminal_state_version": state.get("state_version"),
        "master_turns": len(master_rows),
        "master_sessions": len(master_sessions),
        "master_conversation_url": master_urls[0],
        "worker_turns": len(worker_rows),
        "worker_sessions": len(worker_sessions),
        "worker_events_acked": len(events),
        "ledger_rows": len(ledger),
        "transport_rows": len(transport),
        "closed_hwnds": closed,
        "d_resume_reason": second_reason,
        "master_rotation_count": int(binding.get("rotation_count") or 0),
    }


async def _run(root: Path, project_id: str, master_ttl_seconds: int, timeout_seconds: int):
    if root.exists():
        raise RuntimeError("E2E_ROOT_ALREADY_EXISTS:" + str(root))
    root.mkdir(parents=True, exist_ok=False)
    root_objective, acceptance, alpha, beta = _contract()
    ProjectBootstrapV3(root).create(
        project_id=project_id,
        root_objective=root_objective,
        acceptance_criteria=acceptance,
    )

    state = ProjectStateStore(root / "project-state.json")
    contract = _read_json(root / "project-contract.json")
    leases = MasterWindowLeaseStore(root / "master-window-lease.json")
    queue = WorkerEventQueueV3(root / "worker-events-v3")
    sessions = SessionRegistryV3(root / "sessions-v3.json")
    outbox = root / "master-worker-v3-outbox"
    watchdog = ContinuationWatchdog(state, leases, outbox, project_contract=contract)
    pump = WorkerEventPumpV3(state, leases, queue, outbox)
    transition = MasterStateTransitionV3(state, leases, queue)
    driver = WindowsMcpActorDriverV3(
        url_timeout_seconds=30,
        url_poll_seconds=0.5,
        page_wait_seconds=3,
        poll_page_wait_seconds=2,
    )
    backend = ActorGuiBackendV3(driver)
    transport = DurableActorTransportV3(root / "actor-transport-v3.json", backend)
    relay = ParallelMasterWorkerRelayV3(
        root,
        sessions,
        transport,
        master_transition=transition,
        max_inflight=3,
        max_workers=3,
    )
    coordinator = MasterWorkerCoordinatorV3(
        project_id,
        state,
        leases,
        watchdog,
        pump,
        relay,
        outbox,
        master_ttl_seconds=master_ttl_seconds,
    )

    deadline = time.monotonic() + timeout_seconds
    tick_count = 0
    last = None
    while time.monotonic() < deadline:
        tick_count += 1
        last = await coordinator.run_once()
        current = state.load()
        if current.get("status") == "HARD_BLOCKED":
            raise RuntimeError("E2E_HARD_BLOCKED:" + json.dumps(last, ensure_ascii=True, sort_keys=True))
        if current.get("status") == "COMPLETE":
            proof = _validate(root, project_id, alpha, beta)
            archived = ProjectLifecycleV3(root).archive_terminal()
            archive_path = Path(archived["archive_path"])
            archived_state = ProjectStateStore(archive_path / "project-state.json").load()
            if archived_state.get("status") != "COMPLETE" or archived_state.get("project_id") != project_id:
                raise RuntimeError("E2E_ARCHIVE_STATE_INVALID")
            rotation = _read_json(root.parent / "project-rotation.json")
            if rotation.get("phase") != "ARCHIVED" or rotation.get("previous_project_id") != project_id:
                raise RuntimeError("E2E_ARCHIVE_ROTATION_INVALID")
            return {
                "status": "PASS",
                "project_id": project_id,
                "ticks": tick_count,
                "source_module_root": str(MODULE_ROOT),
                "archive_path": str(archive_path),
                **proof,
            }
        await asyncio.sleep(2)
    raise TimeoutError("E2E_TERMINAL_TIMEOUT:" + json.dumps(last, ensure_ascii=True, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--master-ttl-seconds", type=int, default=120)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--clean-root", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    if args.clean_root and root.exists():
        shutil.rmtree(root)
    if args.master_ttl_seconds < 30:
        raise ValueError("E2E_MASTER_TTL_TOO_SHORT")
    result = asyncio.run(_run(root, args.project_id, args.master_ttl_seconds, args.timeout_seconds))
    print("PERSISTENT_A_DYNAMIC_WORKERS_E2E=" + json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
