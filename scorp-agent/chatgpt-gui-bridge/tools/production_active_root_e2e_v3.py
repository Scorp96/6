from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import sys
import time
from pathlib import Path

DEFAULT_PROD_MODULES = Path(r"C:\ScorpAgent\chatgpt-gui-bridge")
DEFAULT_PROD_ROOT = Path(r"C:\ScorpAgent\state-v3\active")
DEFAULT_HEALTH_PATH = Path(r"C:\ScorpAgent\chatgpt-gui-bridge-state\health.json")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_files(path):
    path = Path(path)
    if not path.is_dir():
        return []
    return [_read_json(file) for file in sorted(path.glob("*.json"))]


def _assignment(name: str):
    task = (
        f"Return HANDOFF only. Top-level summary must be exactly final-e2e-{name}. "
        f'Top-level evidence must be exactly ["{name}-proof"]. '
        f'Top-level resource_scope must be exactly ["final/{name}"]. '
        "Top-level remaining and blockers must both be empty arrays."
    )
    return {
        "objective_sha256": _sha(task),
        "task": task,
        "resource_scope": [f"final/{name}"],
        "success_criteria": "Return exactly the bounded HANDOFF payload required by task.",
    }


def _contract():
    alpha = _assignment("alpha")
    beta = _assignment("beta")
    gamma = _assignment("gamma")
    gamma_json = json.dumps(gamma, ensure_ascii=False, separators=(",", ":"))
    alpha_json = json.dumps(alpha, ensure_ascii=False, separators=(",", ":"))
    beta_json = json.dumps(beta, ensure_ascii=False, separators=(",", ":"))

    event_policy = (
        "For every WORKER_HANDOFF, require worker_guard.verdict ALIGNED and validate the worker summary/evidence/resource_scope "
        "against its bound assignment. Treat payload.project_state as the state BEFORE the current worker event is committed. "
        "If project_state.current_phase is FINAL_E2E_AB_ACTIVE and project_state.evidence is empty, this is the first worker event: "
        "the worker must be alpha or beta; return DISPATCH with exactly one assignment, gamma, and no other assignment. "
        f"Gamma assignment must be exactly {gamma_json}. "
        "That DISPATCH state_patch must set current_phase FINAL_E2E_GAMMA_ACTIVE, evidence to a one-item array containing only the current worker summary, "
        "and next_exact_action to this same worker-event policy. "
        "If project_state.current_phase is FINAL_E2E_GAMMA_ACTIVE and project_state.evidence has exactly one item, this is the second worker event: "
        "return CONTINUE with state_patch.current_phase FINAL_E2E_TWO_FUSED, evidence equal to prior project_state.evidence followed by the current worker summary, "
        "and next_exact_action to this same worker-event policy. "
        "If project_state.current_phase is FINAL_E2E_TWO_FUSED and project_state.evidence has exactly two items, this is the third worker event: "
        "return DRAIN with state_patch.current_phase FINAL_E2E_HANDOFF_READY, evidence equal to prior project_state.evidence followed by the current worker summary, "
        'tests ["final-worker-fusion-guard-pass"], and next_exact_action exactly "D must resume persistent A in the same canonical conversation; then A must TERMINAL COMPLETE." '
        "Never dispatch any worker other than the exact gamma assignment above."
    )

    root_objective = (
        "This is one unattended installed-production E2E acceptance canary. Do no unrelated work. Persistent Master identity is always A. "
        "On RESUME_MASTER when payload.project_state.current_phase is BOOTSTRAPPED, return DISPATCH with exactly two assignments and no others: "
        f"alpha exactly {alpha_json} and beta exactly {beta_json}. "
        "The DISPATCH state_patch must set current_phase FINAL_E2E_AB_ACTIVE, evidence to [], and next_exact_action to the worker-event policy below. "
        "On CONTINUE_CORE_AFTER_DISPATCH, return WAIT and dispatch no additional workers. "
        + event_policy
        + " On a later RESUME_MASTER when payload.project_state.current_phase is FINAL_E2E_HANDOFF_READY, verify previous_master_lease.status is DRAINED, "
        "verify project_state.active_workers is empty and project_state.evidence contains final-e2e-alpha, final-e2e-beta, and final-e2e-gamma exactly once each, "
        "then return TERMINAL with terminal_status COMPLETE. That TERMINAL state_patch must set current_phase FINAL_E2E_COMPLETE, "
        'next_exact_action "archive terminal final acceptance", '
        'evidence ["final-e2e-alpha","final-e2e-beta","final-e2e-gamma","d-reactivated-same-a"], '
        'and tests ["final-worker-fusion-guard-pass","persistent-a-worker-pool-d-resume-e2e-pass"]. '
        "Never TERMINAL before DRAIN and the subsequent RESUME_MASTER."
    )

    acceptance = (
        "PASS only if the installed production daemon runs without user continuation after bootstrap; one persistent Master A uses one canonical ChatGPT conversation "
        "across all Master turns; A initially dispatches alpha and beta; after the first guarded worker HANDOFF A dispatches gamma; gamma reuses the released persistent "
        "Worker conversation slot so exactly three logical workers use exactly two Worker conversation URLs; all three WorkerEvents are guard-ALIGNED and ACKED; "
        "A fuses all results, DRAINs, deterministic D emits a later RESUME_MASTER with reason LEASE_ENDED, resumed A reuses the same canonical A conversation and "
        "TERMINALs COMPLETE; Worker pool leases are fully released while pooled conversation URLs remain; no transport row is AMBIGUOUS, TIMED_OUT, RETRYABLE, or "
        "SUBMITTING at terminal; no retry storm creates extra conversation URLs; terminal project archives validly and production health returns IDLE."
    )
    return root_objective, acceptance, {"alpha": alpha, "beta": beta, "gamma": gamma}


def _all_windows_closed(transport):
    user32 = ctypes.windll.user32
    count = 0
    for turn_id, row in transport.items():
        handle = (row or {}).get("handle") if isinstance(row, dict) else None
        hwnd = handle.get("window_handle") if isinstance(handle, dict) else None
        if type(hwnd) is not int or hwnd <= 0:
            raise RuntimeError("FINAL_E2E_WINDOW_HANDLE_MISSING:" + str(turn_id))
        if user32.IsWindow(hwnd):
            raise RuntimeError("FINAL_E2E_WINDOW_LEAK:" + str(turn_id))
        count += 1
    return count


def _verify_transport_resources(transport, *, is_window=None):
    if not isinstance(transport, dict) or not transport:
        raise RuntimeError("FINAL_E2E_TRANSPORT_RESOURCES_EMPTY")

    hwnd_rows = []
    for turn_id, row in transport.items():
        handle = (row or {}).get("handle") if isinstance(row, dict) else None
        hwnd = handle.get("window_handle") if isinstance(handle, dict) else None
        if hwnd is not None:
            hwnd_rows.append((turn_id, hwnd))

    if hwnd_rows:
        if len(hwnd_rows) != len(transport):
            raise RuntimeError("FINAL_E2E_TRANSPORT_RESOURCE_MIXED")
        if is_window is None:
            is_window = ctypes.windll.user32.IsWindow
        for turn_id, hwnd in hwnd_rows:
            if type(hwnd) is not int or hwnd <= 0:
                raise RuntimeError("FINAL_E2E_WINDOW_HANDLE_MISSING:" + str(turn_id))
            if is_window(hwnd):
                raise RuntimeError("FINAL_E2E_WINDOW_LEAK:" + str(turn_id))
        return {
            "kind": "windows-mcp",
            "resource_count": len(hwnd_rows),
            "conversation_urls": [],
        }

    urls = set()
    for turn_id, row in transport.items():
        if not isinstance(row, dict) or row.get("state") != "COMPLETED":
            raise RuntimeError("FINAL_E2E_CHROME_USE_TRANSPORT_NOT_COMPLETED:" + str(turn_id))
        handle = row.get("handle")
        if not isinstance(handle, dict):
            raise RuntimeError("FINAL_E2E_CHROME_USE_HANDLE_INVALID:" + str(turn_id))
        submission_id = str(row.get("submission_id") or "")
        actor_kind = str(handle.get("actor_kind") or "").upper()
        url = str(row.get("conversation_url") or "")
        if (
            not submission_id
            or str(handle.get("submission_id") or "") != submission_id
            or str(handle.get("turn_id") or "") != str(turn_id)
            or actor_kind not in {"MASTER", "WORKER"}
            or str(handle.get("conversation_url") or "") != url
            or not url.startswith("https://chatgpt.com/c/")
        ):
            raise RuntimeError("FINAL_E2E_CHROME_USE_HANDLE_INVALID:" + str(turn_id))
        urls.add(url)
    return {
        "kind": "chrome-use",
        "resource_count": len(transport),
        "conversation_urls": sorted(urls),
    }


def _wait_for_terminal(ProjectStateStore, root: Path, health_path: Path, timeout_seconds: int):
    deadline = time.monotonic() + timeout_seconds
    last_health = None
    while time.monotonic() < deadline:
        state_path = root / "project-state.json"
        if not state_path.is_file():
            raise RuntimeError("FINAL_E2E_STATE_DISAPPEARED")
        state = ProjectStateStore(state_path).load()
        if health_path.is_file():
            last_health = _read_json(health_path)
        if state.get("status") == "HARD_BLOCKED":
            raise RuntimeError("FINAL_E2E_HARD_BLOCKED:" + json.dumps(last_health, ensure_ascii=True, sort_keys=True))
        if state.get("status") == "COMPLETE":
            return state, last_health
        time.sleep(2)
    error = None if not isinstance(last_health, dict) else last_health.get("error")
    raise TimeoutError("FINAL_E2E_TERMINAL_TIMEOUT:" + str(error or ""))


def _validate_terminal(root: Path, project_id: str, expected_assignments: dict, ProjectStateStore, SessionRegistryV3):
    state = ProjectStateStore(root / "project-state.json").load()
    if state.get("status") != "COMPLETE" or state.get("current_phase") != "FINAL_E2E_COMPLETE":
        raise RuntimeError("FINAL_E2E_TERMINAL_STATE_INVALID:" + json.dumps(state, ensure_ascii=True, sort_keys=True))
    if state.get("active_workers"):
        raise RuntimeError("FINAL_E2E_ACTIVE_WORKERS_REMAIN")
    if state.get("evidence") != [
        "final-e2e-alpha", "final-e2e-beta", "final-e2e-gamma", "d-reactivated-same-a"
    ]:
        raise RuntimeError("FINAL_E2E_FINAL_EVIDENCE_INVALID")

    inbox = _json_files(root / "master-worker-v3-inbox")
    master_rows = [r for r in inbox if str(((r.get("turn") or {}).get("actor_kind") or "")).upper() == "MASTER"]
    worker_rows = [r for r in inbox if str(((r.get("turn") or {}).get("actor_kind") or "")).upper() == "WORKER"]
    if len(worker_rows) != 3:
        raise RuntimeError("FINAL_E2E_WORKER_TURN_COUNT_INVALID:" + str(len(worker_rows)))
    if len(master_rows) < 6:
        raise RuntimeError("FINAL_E2E_MASTER_TURN_COUNT_TOO_LOW:" + str(len(master_rows)))
    if any((r.get("turn") or {}).get("actor_id") != "A" for r in master_rows):
        raise RuntimeError("FINAL_E2E_MASTER_IDENTITY_DRIFT")

    master_urls = [str(r.get("conversation_url") or "") for r in master_rows]
    if any(not u for u in master_urls) or len(set(master_urls)) != 1:
        raise RuntimeError("FINAL_E2E_MASTER_CONVERSATION_NOT_PERSISTENT:" + json.dumps(master_urls))
    master_sessions = {str((r.get("turn") or {}).get("session_id") or "") for r in master_rows}
    if len(master_sessions) != 2:
        raise RuntimeError("FINAL_E2E_MASTER_SESSION_COUNT_INVALID:" + str(len(master_sessions)))

    resume_rows = [
        r for r in master_rows
        if str((((r.get("turn") or {}).get("payload") or {}).get("event") or "")) == "RESUME_MASTER"
    ]
    if len(resume_rows) != 2:
        raise RuntimeError("FINAL_E2E_RESUME_COUNT_INVALID:" + str(len(resume_rows)))
    reasons = [
        str((((r.get("turn") or {}).get("payload") or {}).get("reason") or ""))
        for r in resume_rows
    ]
    if set(reasons) != {"NO_ACTIVE_MASTER", "LEASE_ENDED"}:
        raise RuntimeError("FINAL_E2E_RESUME_REASONS_INVALID:" + json.dumps(reasons))
    initial_resume = next(r for r in resume_rows if str((((r.get("turn") or {}).get("payload") or {}).get("reason") or "")) == "NO_ACTIVE_MASTER")
    final_resume = next(r for r in resume_rows if str((((r.get("turn") or {}).get("payload") or {}).get("reason") or "")) == "LEASE_ENDED")
    if str((initial_resume.get("response") or {}).get("kind") or "").upper() != "DISPATCH":
        raise RuntimeError("FINAL_E2E_INITIAL_MASTER_NOT_DISPATCH")
    if str((final_resume.get("response") or {}).get("kind") or "").upper() != "TERMINAL":
        raise RuntimeError("FINAL_E2E_RESUMED_MASTER_NOT_TERMINAL")

    expected_by_hash = {row["objective_sha256"]: row for row in expected_assignments.values()}
    observed_workers = {}
    worker_ids = set()
    worker_sessions = set()
    worker_urls = []
    for row in worker_rows:
        turn = row.get("turn") or {}
        objective = str(turn.get("objective_sha256") or "")
        expected = expected_by_hash.get(objective)
        if expected is None:
            raise RuntimeError("FINAL_E2E_WORKER_OBJECTIVE_UNKNOWN:" + objective)
        response = row.get("response") or {}
        name = expected["resource_scope"][0].split("/", 1)[1]
        if str(response.get("kind") or "").upper() != "HANDOFF":
            raise RuntimeError("FINAL_E2E_WORKER_NOT_HANDOFF:" + name)
        if response.get("summary") != f"final-e2e-{name}":
            raise RuntimeError("FINAL_E2E_WORKER_SUMMARY_INVALID:" + name)
        if response.get("evidence") != [f"{name}-proof"]:
            raise RuntimeError("FINAL_E2E_WORKER_EVIDENCE_INVALID:" + name)
        if response.get("resource_scope") != [f"final/{name}"]:
            raise RuntimeError("FINAL_E2E_WORKER_SCOPE_INVALID:" + name)
        observed_workers[objective] = row
        worker_ids.add(str(turn.get("actor_id") or ""))
        worker_sessions.add(str(turn.get("session_id") or ""))
        worker_urls.append(str(row.get("conversation_url") or ""))

    if set(observed_workers) != set(expected_by_hash):
        raise RuntimeError("FINAL_E2E_WORKER_BINDINGS_INCOMPLETE")
    if len(worker_ids) != 3 or len(worker_sessions) != 3:
        raise RuntimeError("FINAL_E2E_LOGICAL_WORKERS_NOT_EPHEMERAL")
    if any(not u for u in worker_urls) or len(set(worker_urls)) != 2:
        raise RuntimeError("FINAL_E2E_WORKER_POOL_REUSE_INVALID:" + json.dumps(worker_urls))
    if master_urls[0] in set(worker_urls):
        raise RuntimeError("FINAL_E2E_MASTER_WORKER_CONVERSATION_COLLISION")

    event_master_rows = [
        r for r in master_rows
        if str((((r.get("turn") or {}).get("payload") or {}).get("event") or "")) == "WORKER_HANDOFF"
    ]
    if len(event_master_rows) != 3:
        raise RuntimeError("FINAL_E2E_WORKER_EVENT_MASTER_COUNT_INVALID:" + str(len(event_master_rows)))
    response_kinds = []
    for row in event_master_rows:
        payload = (row.get("turn") or {}).get("payload") or {}
        if ((payload.get("worker_guard") or {}).get("verdict")) != "ALIGNED":
            raise RuntimeError("FINAL_E2E_WORKER_GUARD_NOT_ALIGNED")
        response_kinds.append(str((row.get("response") or {}).get("kind") or "").upper())
    if sorted(response_kinds) != ["CONTINUE", "DISPATCH", "DRAIN"]:
        raise RuntimeError("FINAL_E2E_MASTER_FUSION_KINDS_INVALID:" + json.dumps(response_kinds))

    core_rows = [
        r for r in master_rows
        if str((((r.get("turn") or {}).get("payload") or {}).get("event") or "")) == "CONTINUE_CORE_AFTER_DISPATCH"
    ]
    if len(core_rows) != 2 or any(str((r.get("response") or {}).get("kind") or "").upper() != "WAIT" for r in core_rows):
        raise RuntimeError("FINAL_E2E_CORE_WAIT_INVALID")

    events = _json_files(root / "worker-events-v3" / "events")
    if len(events) != 3 or any(e.get("state") != "ACKED" or e.get("response_kind") != "HANDOFF" for e in events):
        raise RuntimeError("FINAL_E2E_WORKER_EVENTS_NOT_ACKED")

    ledger = _read_json(root / "master-worker-v3-ledger.json")
    if not ledger or any((row or {}).get("state") != "ROUTED" for row in ledger.values()):
        raise RuntimeError("FINAL_E2E_RELAY_LEDGER_NOT_FULLY_ROUTED")
    transport = _read_json(root / "actor-transport-v3.json")
    if len(transport) != len(ledger):
        raise RuntimeError("FINAL_E2E_TRANSPORT_LEDGER_COUNT_MISMATCH")
    bad_transport = {k: (v or {}).get("state") for k, v in transport.items() if (v or {}).get("state") != "COMPLETED"}
    if bad_transport:
        raise RuntimeError("FINAL_E2E_TRANSPORT_NOT_COMPLETED:" + json.dumps(bad_transport, sort_keys=True))

    pool = _read_json(root / "worker-conversation-pool-v3.json")
    slots = pool.get("slots") or {}
    if len(slots) != 3:
        raise RuntimeError("FINAL_E2E_WORKER_POOL_SLOT_COUNT_INVALID")
    if any((slot or {}).get("lease") is not None for slot in slots.values()):
        raise RuntimeError("FINAL_E2E_WORKER_POOL_LEASE_REMAINS")
    pooled_urls = [str((slot or {}).get("conversation_url") or "") for slot in slots.values() if (slot or {}).get("conversation_url")]
    if len(set(pooled_urls)) != 2 or set(pooled_urls) != set(worker_urls):
        raise RuntimeError("FINAL_E2E_WORKER_POOL_URLS_INVALID:" + json.dumps(pooled_urls))

    resource = _read_json(root / "chat-resource-v3.json")
    throttle_not_before = str(resource.get("throttle_not_before") or "") or None

    sessions = SessionRegistryV3(root / "sessions-v3.json")
    master_binding = sessions.get_master_conversation(project_id)
    if not isinstance(master_binding, dict) or master_binding.get("conversation_url") != master_urls[0]:
        raise RuntimeError("FINAL_E2E_MASTER_BINDING_INVALID")
    if int(master_binding.get("rotation_count") or 0) != 0:
        raise RuntimeError("FINAL_E2E_MASTER_CONVERSATION_ROTATED")

    lease = _read_json(root / "master-window-lease.json").get(project_id)
    if not isinstance(lease, dict) or lease.get("status") != "RELEASED":
        raise RuntimeError("FINAL_E2E_FINAL_MASTER_LEASE_INVALID")
    final_session = str((final_resume.get("turn") or {}).get("session_id") or "")
    if lease.get("session_id") != final_session:
        raise RuntimeError("FINAL_E2E_FINAL_MASTER_LEASE_SESSION_INVALID")

    transport_resources = _verify_transport_resources(transport)
    return {
        "state_version": state.get("state_version"),
        "master_turns": len(master_rows),
        "master_sessions": len(master_sessions),
        "master_conversation_url": master_urls[0],
        "worker_turns": len(worker_rows),
        "logical_worker_ids": len(worker_ids),
        "worker_conversation_urls": sorted(set(worker_urls)),
        "worker_events_acked": len(events),
        "ledger_rows": len(ledger),
        "transport_rows": len(transport),
        "transport_kind": transport_resources["kind"],
        "transport_resource_count": transport_resources["resource_count"],
        "transport_conversation_urls": transport_resources["conversation_urls"],
        "closed_hwnds": transport_resources["resource_count"] if transport_resources["kind"] == "windows-mcp" else 0,
        "master_rotation_count": int(master_binding.get("rotation_count") or 0),
        "throttle_not_before": throttle_not_before,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prod-modules", default=str(DEFAULT_PROD_MODULES))
    parser.add_argument("--root", default=str(DEFAULT_PROD_ROOT))
    parser.add_argument("--health-path", default=str(DEFAULT_HEALTH_PATH))
    parser.add_argument("--project-id", default="production-final-unattended-v3")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()

    prod_modules = Path(args.prod_modules)
    root = Path(args.root)
    health_path = Path(args.health_path)
    if not prod_modules.is_dir():
        raise RuntimeError("FINAL_E2E_MODULES_MISSING")
    if str(prod_modules) not in sys.path:
        sys.path.insert(0, str(prod_modules))

    from project_bootstrap_v3 import ProjectBootstrapV3
    from project_lifecycle_v3 import ProjectLifecycleV3
    from project_state_v3 import ProjectStateStore
    from session_registry_v3 import SessionRegistryV3

    required_modules = [
        "chat_resource_manager_v3.py",
        "worker_conversation_pool_v3.py",
        "parallel_master_worker_relay_v3.py",
        "durable_actor_transport_v3.py",
        "windows_mcp_actor_driver_v3.py",
    ]
    missing = [name for name in required_modules if not (prod_modules / name).is_file()]
    if missing:
        raise RuntimeError("FINAL_E2E_INSTALLED_MODULES_MISSING:" + ",".join(missing))

    root.mkdir(parents=True, exist_ok=True)
    forbidden = [
        root / "project-state.json",
        root / "project-contract.json",
        root / "master-worker-v3-ledger.json",
        root / "actor-transport-v3.json",
    ]
    if any(path.exists() for path in forbidden):
        raise RuntimeError("FINAL_E2E_ACTIVE_ROOT_NOT_CLEAN")

    root_objective, acceptance, assignments = _contract()
    created = ProjectBootstrapV3(root).create(
        project_id=args.project_id,
        root_objective=root_objective,
        acceptance_criteria=acceptance,
    )
    state0 = created["state"]
    if state0.get("status") != "ACTIVE" or state0.get("state_version") != 0:
        raise RuntimeError("FINAL_E2E_BOOTSTRAP_INVALID")

    terminal, last_health = _wait_for_terminal(ProjectStateStore, root, health_path, args.timeout_seconds)
    if terminal.get("status") != "COMPLETE":
        raise RuntimeError("FINAL_E2E_NOT_COMPLETE")

    proof = _validate_terminal(root, args.project_id, assignments, ProjectStateStore, SessionRegistryV3)

    archived = ProjectLifecycleV3(root).archive_terminal()
    archive_path = Path(archived["archive_path"])
    archived_state = ProjectStateStore(archive_path / "project-state.json").load()
    if archived_state.get("status") != "COMPLETE" or archived_state.get("project_id") != args.project_id:
        raise RuntimeError("FINAL_E2E_ARCHIVED_STATE_INVALID")
    rotation = _read_json(root.parent / "project-rotation.json")
    if rotation.get("phase") != "ARCHIVED" or rotation.get("previous_project_id") != args.project_id:
        raise RuntimeError("FINAL_E2E_ROTATION_INVALID")

    deadline = time.monotonic() + 60
    final_health = None
    while time.monotonic() < deadline:
        if health_path.is_file():
            final_health = _read_json(health_path)
            if final_health.get("status") == "IDLE" and final_health.get("error") is None:
                break
        time.sleep(2)
    else:
        raise RuntimeError("FINAL_E2E_HEALTH_NOT_IDLE:" + str(final_health))

    print(
        "FINAL_UNATTENDED_PRODUCTION_E2E="
        + json.dumps(
            {
                "status": "PASS",
                "project_id": args.project_id,
                "archive_path": str(archive_path),
                "rotation_phase": rotation.get("phase"),
                "last_health_before_archive": last_health,
                "final_health": final_health,
                **proof,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
