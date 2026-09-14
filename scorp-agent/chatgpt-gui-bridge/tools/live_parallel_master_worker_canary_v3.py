import asyncio
import ctypes
import datetime as dt
import hashlib
import json
from pathlib import Path

from actor_gui_backend_v3 import ActorGuiBackendV3
from continuation_watchdog_v3 import ContinuationWatchdog
from durable_actor_transport_v3 import DurableActorTransportV3
from master_state_transition_v3 import MasterStateTransitionV3
from master_window_lease_v3 import MasterWindowLeaseStore
from parallel_master_worker_relay_v3 import ParallelMasterWorkerRelayV3
from project_state_v3 import ProjectStateStore
from session_registry_v3 import SessionRegistryV3
from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3
from worker_event_queue_v3 import WorkerEventQueueV3

UTC = dt.timezone.utc
ROOT = Path(r"C:\ScorpAgent\state-v3-live-canary\parallel-g63")
PROJECT_ID = "live-parallel-g63"
OBJ1 = "1" * 64
OBJ2 = "2" * 64
WORKER_EXPECTATIONS = {
    OBJ1: {"prefix": "W1", "summary": "worker-one-complete"},
    OBJ2: {"prefix": "W2", "summary": "worker-two-complete"},
}


def _validate_worker_handoff(response, objective_sha256):
    expected = WORKER_EXPECTATIONS.get(str(objective_sha256 or ""))
    if expected is None:
        raise RuntimeError("LIVE_CANARY_WORKER_OBJECTIVE_UNKNOWN")
    if not isinstance(response, dict) or str(response.get("kind") or "").upper() != "HANDOFF":
        raise RuntimeError("LIVE_CANARY_WORKER_NOT_HANDOFF")
    labels = [f"{expected['prefix']}-{i:03d}" for i in range(1, 241)]
    if response.get("evidence") != labels:
        raise RuntimeError("LIVE_CANARY_WORKER_EVIDENCE_INVALID")
    if response.get("summary") != expected["summary"]:
        raise RuntimeError("LIVE_CANARY_WORKER_SUMMARY_INVALID")
    return {
        "prefix": expected["prefix"],
        "evidence_count": len(labels),
        "summary": expected["summary"],
    }


def _select_overlap_probe_worker(workers):
    parsed = []
    for turn_id, row in workers:
        submitted_at = str((row or {}).get("submitted_at") or "").strip()
        if not submitted_at:
            raise RuntimeError("LIVE_CANARY_WORKER_SUBMITTED_AT_MISSING")
        try:
            stamp = dt.datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
        except Exception as exc:
            raise RuntimeError("LIVE_CANARY_WORKER_SUBMITTED_AT_INVALID") from exc
        if stamp.tzinfo is None:
            raise RuntimeError("LIVE_CANARY_WORKER_SUBMITTED_AT_INVALID")
        parsed.append((stamp.astimezone(UTC), turn_id, row))
    if not parsed:
        raise RuntimeError("LIVE_CANARY_WORKER_SUBMITTED_AT_MISSING")
    stamps = [stamp for stamp, _, _ in parsed]
    if len(set(stamps)) != len(stamps):
        raise RuntimeError("LIVE_CANARY_WORKER_SUBMITTED_AT_AMBIGUOUS")
    _, turn_id, row = max(parsed, key=lambda item: item[0])
    return turn_id, row


def _verify_transport_windows_closed(transport_rows, is_window_fn):
    if not isinstance(transport_rows, dict):
        raise RuntimeError("LIVE_CANARY_TRANSPORT_INVALID")
    count = 0
    for turn_id, row in transport_rows.items():
        handle = (row or {}).get("handle") if isinstance(row, dict) else None
        hwnd = handle.get("window_handle") if isinstance(handle, dict) else None
        if type(hwnd) is not int or hwnd <= 0:
            raise RuntimeError("LIVE_CANARY_WINDOW_HANDLE_MISSING")
        if bool(is_window_fn(hwnd)):
            raise RuntimeError("LIVE_CANARY_WINDOW_LEAK")
        count += 1
    return count


def _hash_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _url_hash(url):
    return hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:16]


async def main():
    if ROOT.exists():
        raise RuntimeError("LIVE_CANARY_ROOT_ALREADY_EXISTS")
    ROOT.mkdir(parents=True)

    goal = "Prove bounded live GPT parallelism: persistent master A dispatches exactly two dynamic workers while A-core continues in a distinct conversation."
    acceptance = "Exactly two workers plus A-core are concurrently submitted; the latest-submitted worker remains pending after A-core submit; both workers eventually HANDOFF with exact bounded evidence; no ambiguous transport state or duplicate submit."
    next_action = (
        "LIVE_CANARY_G63. Reply DISPATCH with exactly two assignments and no others. "
        f"Assignment 1 objective_sha256 must be {OBJ1}; task: return HANDOFF only after producing a top-level evidence array containing exactly 240 short labels W1-001 through W1-240 in order, plus top-level summary exactly worker-one-complete. "
        f"Assignment 2 objective_sha256 must be {OBJ2}; task: return HANDOFF only after producing a top-level evidence array containing exactly 240 short labels W2-001 through W2-240 in order, plus top-level summary exactly worker-two-complete. "
        "Your DISPATCH state_patch must set current_phase to LIVE_PARALLEL_WORKERS and next_exact_action to: LIVE_CANARY_G63 A-core must not dispatch more workers; return WAIT while active_workers is non-empty."
    )
    initial = {
        "protocol_version": "scorp.project-state/v1",
        "project_id": PROJECT_ID,
        "master_identity": "A",
        "state_version": 0,
        "goal_contract_sha256": _hash_text(goal),
        "acceptance_sha256": _hash_text(acceptance),
        "status": "ACTIVE",
        "current_phase": "LIVE_CANARY_DISPATCH",
        "next_exact_action": next_action,
        "active_master_window": None,
        "active_workers": [],
        "completed": [],
        "blocked": [],
        "evidence": [],
        "commits": [],
        "tests": [],
    }

    state = ProjectStateStore(ROOT / "project-state.json")
    state.create(initial)
    leases = MasterWindowLeaseStore(ROOT / "master-window-lease.json")
    outbox = ROOT / "master-worker-v3-outbox"
    watchdog = ContinuationWatchdog(state, leases, outbox)
    now = dt.datetime.now(UTC)
    resume = watchdog.run_once(now=now)
    if resume.get("status") not in {"QUEUED", "ALREADY_QUEUED"}:
        raise RuntimeError("LIVE_CANARY_RESUME_NOT_QUEUED")
    leases.acquire(
        PROJECT_ID,
        resume["session_id"],
        resume["turn_id"],
        0,
        now=now,
        ttl_seconds=1800,
    )

    queue = WorkerEventQueueV3(ROOT / "worker-events-v3")
    transition = MasterStateTransitionV3(state, leases, queue)
    sessions = SessionRegistryV3(ROOT / "sessions-v3.json")
    driver = WindowsMcpActorDriverV3(
        url_timeout_seconds=30,
        url_poll_seconds=0.5,
        page_wait_seconds=3,
        poll_page_wait_seconds=2,
    )
    backend = ActorGuiBackendV3(driver)
    transport = DurableActorTransportV3(ROOT / "actor-transport-v3.json", backend)
    relay = ParallelMasterWorkerRelayV3(
        ROOT,
        sessions,
        transport,
        master_transition=transition,
        max_inflight=3,
    )

    first = await relay.run_tick(now=dt.datetime.now(UTC))
    if first["submitted"] != 1 or first["inflight"] != 1:
        raise RuntimeError("LIVE_CANARY_INITIAL_MASTER_SUBMIT_INVALID")

    dispatch_tick = None
    deadline = asyncio.get_running_loop().time() + 240
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(2)
        tick = await relay.run_tick(now=dt.datetime.now(UTC))
        ledger = _load(ROOT / "master-worker-v3-ledger.json")
        routed_masters = [
            (turn_id, row) for turn_id, row in ledger.items()
            if isinstance(row, dict) and row.get("actor_kind") == "MASTER" and row.get("state") == "ROUTED"
        ]
        inflight = [
            (turn_id, row) for turn_id, row in ledger.items()
            if isinstance(row, dict) and row.get("state") == "GUI_SUBMITTED"
        ]
        workers = [(turn_id, row) for turn_id, row in inflight if row.get("actor_kind") == "WORKER"]
        masters = [(turn_id, row) for turn_id, row in inflight if row.get("actor_kind") == "MASTER"]
        if routed_masters and len(workers) == 2 and len(masters) == 1 and len(inflight) == 3:
            dispatch_tick = tick
            break
    if dispatch_tick is None:
        raise RuntimeError("LIVE_CANARY_THREE_WAY_INFLIGHT_NOT_REACHED")

    ledger = _load(ROOT / "master-worker-v3-ledger.json")
    inflight = [(turn_id, row) for turn_id, row in ledger.items() if isinstance(row, dict) and row.get("state") == "GUI_SUBMITTED"]
    workers = sorted([(turn_id, row) for turn_id, row in inflight if row.get("actor_kind") == "WORKER"])
    masters = sorted([(turn_id, row) for turn_id, row in inflight if row.get("actor_kind") == "MASTER"])
    urls = [row.get("conversation_url") for _, row in inflight]
    if len(urls) != 3 or any(not url for url in urls) or len(set(urls)) != 3:
        raise RuntimeError("LIVE_CANARY_CONVERSATIONS_NOT_DISTINCT")

    overlap_turn_id, overlap_worker_row = _select_overlap_probe_worker(workers)
    overlap_probe = await transport.poll(overlap_turn_id, timeout_seconds=1800)
    if str(overlap_probe.get("status") or "").upper() != "PENDING":
        raise RuntimeError("LIVE_CANARY_TEMPORAL_OVERLAP_NOT_OBSERVED")

    target_turns = [workers[0][0], workers[1][0], masters[0][0]]
    completed = {}
    deadline = asyncio.get_running_loop().time() + 360
    while asyncio.get_running_loop().time() < deadline and len(completed) < 3:
        for turn_id in target_turns:
            if turn_id in completed:
                continue
            result = await transport.poll(turn_id, timeout_seconds=1800)
            status = str(result.get("status") or "").upper()
            if status == "AMBIGUOUS":
                raise RuntimeError("LIVE_CANARY_TRANSPORT_AMBIGUOUS")
            if status == "COMPLETED":
                completed[turn_id] = result
        if len(completed) < 3:
            await asyncio.sleep(2)
    if len(completed) != 3:
        raise RuntimeError("LIVE_CANARY_COMPLETION_TIMEOUT")

    worker_validations = []
    for turn_id, _ in workers:
        response = completed[turn_id].get("response") or {}
        worker_turn = _load(ROOT / "master-worker-v3-outbox" / f"{turn_id}.json")
        worker_validations.append(_validate_worker_handoff(response, worker_turn.get("objective_sha256")))
    master_response = completed[masters[0][0]].get("response") or {}
    if str(master_response.get("kind") or "").upper() != "WAIT":
        raise RuntimeError("LIVE_CANARY_A_CORE_NOT_WAIT")

    routed = await relay.run_tick(now=dt.datetime.now(UTC))
    ledger = _load(ROOT / "master-worker-v3-ledger.json")
    for turn_id in target_turns:
        if (ledger.get(turn_id) or {}).get("state") != "ROUTED":
            raise RuntimeError("LIVE_CANARY_COMPLETED_TURN_NOT_ROUTED")
    pending_events = queue.pending(PROJECT_ID)
    if len(pending_events) != 2:
        raise RuntimeError("LIVE_CANARY_WORKER_EVENT_COUNT_INVALID")

    transport_rows = _load(ROOT / "actor-transport-v3.json")
    if any((row or {}).get("state") == "AMBIGUOUS" for row in transport_rows.values()):
        raise RuntimeError("LIVE_CANARY_AMBIGUOUS_LEDGER_ROW")
    if len(transport_rows) != 4:
        raise RuntimeError("LIVE_CANARY_UNEXPECTED_SUBMISSION_COUNT")
    user32 = ctypes.windll.user32
    closed_hwnds = _verify_transport_windows_closed(
        transport_rows,
        lambda hwnd: bool(user32.IsWindow(hwnd)),
    )

    summary = {
        "status": "PASS",
        "project_id": PROJECT_ID,
        "three_way_inflight": True,
        "temporal_overlap_latest_worker_pending_after_a_core_submit": True,
        "overlap_probe_worker_turn_id": overlap_turn_id,
        "overlap_probe_worker_submitted_at": overlap_worker_row["submitted_at"],
        "worker_handoffs": 2,
        "worker_evidence": sorted(worker_validations, key=lambda row: row["prefix"]),
        "a_core_response": "WAIT",
        "worker_events_pending": len(pending_events),
        "transport_rows": len(transport_rows),
        "closed_hwnds": closed_hwnds,
        "ambiguous": 0,
        "conversation_url_hashes": sorted(_url_hash(url) for url in urls),
        "dispatch_tick": dispatch_tick,
        "route_tick": routed,
        "state_version": state.load()["state_version"],
        "root": str(ROOT),
    }
    print("LIVE_PARALLEL_MASTER_WORKER_V3_CANARY=" + json.dumps(summary, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
