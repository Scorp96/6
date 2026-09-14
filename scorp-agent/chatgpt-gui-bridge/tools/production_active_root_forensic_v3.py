import json
from pathlib import Path

ROOT = Path(r"C:\ScorpAgent\state-v3\active")
HEALTH = Path(r"C:\ScorpAgent\chatgpt-gui-bridge-state\health.json")


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"__read_error__": f"{type(exc).__name__}: {exc}"}


def summarize_turn(turn):
    if not isinstance(turn, dict):
        return turn
    payload = turn.get("payload") or {}
    return {
        "turn_id": turn.get("turn_id"),
        "actor_kind": turn.get("actor_kind"),
        "actor_id": turn.get("actor_id"),
        "session_id": turn.get("session_id"),
        "state_version": turn.get("state_version"),
        "event": payload.get("event"),
        "worker_id": payload.get("worker_id"),
        "objective_sha256": turn.get("objective_sha256") or payload.get("objective_sha256"),
    }


def json_dir(path):
    rows = []
    path = Path(path)
    if not path.is_dir():
        return rows
    for file in sorted(path.glob("*.json")):
        value = read_json(file)
        rows.append({"file": file.name, "value": value})
    return rows


def main():
    state = read_json(ROOT / "project-state.json")
    contract = read_json(ROOT / "project-contract.json")
    lease = read_json(ROOT / "master-window-lease.json")
    sessions = read_json(ROOT / "sessions-v3.json")
    ledger = read_json(ROOT / "master-worker-v3-ledger.json", {}) or {}
    transport = read_json(ROOT / "actor-transport-v3.json", {}) or {}
    journal = read_json(ROOT / "actor-response-journal-v3.json", {}) or {}
    health = read_json(HEALTH)

    outbox = []
    for row in json_dir(ROOT / "master-worker-v3-outbox"):
        outbox.append({"file": row["file"], "turn": summarize_turn(row["value"])})

    inbox = []
    for row in json_dir(ROOT / "master-worker-v3-inbox"):
        value = row["value"] or {}
        response = value.get("response") if isinstance(value, dict) else None
        inbox.append({
            "file": row["file"],
            "turn": summarize_turn((value or {}).get("turn") if isinstance(value, dict) else None),
            "response_kind": (response or {}).get("kind") if isinstance(response, dict) else None,
            "terminal_status": (response or {}).get("terminal_status") if isinstance(response, dict) else None,
            "summary": (response or {}).get("summary") if isinstance(response, dict) else None,
            "evidence_count": len((response or {}).get("evidence") or []) if isinstance(response, dict) else None,
        })

    events = []
    for row in json_dir(ROOT / "worker-events-v3" / "events"):
        value = row["value"] or {}
        events.append({
            "file": row["file"],
            "event_id": value.get("event_id") if isinstance(value, dict) else None,
            "state": value.get("state") if isinstance(value, dict) else None,
            "response_kind": value.get("response_kind") if isinstance(value, dict) else None,
            "worker_id": value.get("worker_id") if isinstance(value, dict) else None,
        })

    ledger_summary = {}
    if isinstance(ledger, dict):
        for key, row in ledger.items():
            ledger_summary[key] = {
                "state": (row or {}).get("state") if isinstance(row, dict) else None,
                "actor_kind": (row or {}).get("actor_kind") if isinstance(row, dict) else None,
                "actor_id": (row or {}).get("actor_id") if isinstance(row, dict) else None,
                "submitted_at": (row or {}).get("submitted_at") if isinstance(row, dict) else None,
                "routed_at": (row or {}).get("routed_at") if isinstance(row, dict) else None,
                "superseded_reason": (row or {}).get("superseded_reason") if isinstance(row, dict) else None,
            }

    transport_summary = {}
    if isinstance(transport, dict):
        for key, row in transport.items():
            handle = (row or {}).get("handle") if isinstance(row, dict) else None
            transport_summary[key] = {
                "state": (row or {}).get("state") if isinstance(row, dict) else None,
                "actor_kind": ((row or {}).get("binding") or {}).get("actor_kind") if isinstance(row, dict) else None,
                "conversation_url": ((handle or {}).get("conversation_url") if isinstance(handle, dict) else None) or ((row or {}).get("conversation_url") if isinstance(row, dict) else None),
                "window_handle": (handle or {}).get("window_handle") if isinstance(handle, dict) else None,
                "response_kind": ((row or {}).get("response") or {}).get("kind") if isinstance(row, dict) and isinstance((row or {}).get("response"), dict) else None,
            }

    result = {
        "state": state,
        "contract_project_id": contract.get("project_id") if isinstance(contract, dict) else None,
        "lease": lease,
        "sessions": sessions,
        "health": health,
        "ledger": ledger_summary,
        "transport": transport_summary,
        "journal_turn_ids": sorted(journal.keys()) if isinstance(journal, dict) else None,
        "outbox": outbox,
        "inbox": inbox,
        "worker_events": events,
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
