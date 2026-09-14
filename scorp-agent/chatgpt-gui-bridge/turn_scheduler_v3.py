from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc


class TurnSchedulerV3:
    """Pure deterministic selection policy for validated V3 actor turns."""

    def __init__(self, project_state_store, master_lease_store):
        self.project_state_store = project_state_store
        self.master_lease_store = master_lease_store

    def _now(self, value):
        value = value or dt.datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("TURN_SCHEDULER_NOW_MUST_BE_AWARE")
        return value.astimezone(UTC)

    def _last_routed_actor(self, ledger: dict):
        rows = []
        for turn_id, row in (ledger or {}).items():
            if not isinstance(row, dict) or row.get("state") != "ROUTED":
                continue
            actor_kind = str(row.get("actor_kind") or "").upper()
            if actor_kind not in {"MASTER", "WORKER"}:
                continue
            routed_at = str(row.get("routed_at") or "")
            rows.append((routed_at, str(turn_id), actor_kind))
        if not rows:
            return None
        rows.sort()
        return rows[-1][2]

    def _superseded(self, candidate, reason):
        return {"turn_id": candidate["turn_id"], "reason": reason}

    def select(self, candidates: list[dict], ledger: dict, *, now=None) -> dict:
        now = self._now(now)
        state = self.project_state_store.load()
        if not isinstance(candidates, list):
            raise ValueError("TURN_SCHEDULER_CANDIDATES_INVALID")
        if not isinstance(ledger, dict):
            raise ValueError("TURN_SCHEDULER_LEDGER_INVALID")

        terminal = state.get("status") in {"COMPLETE", "HARD_BLOCKED"}
        if state.get("status") not in {"ACTIVE", "COMPLETE", "HARD_BLOCKED"}:
            raise ValueError("PROJECT_STATE_STATUS_INVALID")

        lease = self.master_lease_store.load(state["project_id"])
        live_master = self.master_lease_store.is_active(state["project_id"], now=now)
        active_session = lease.get("session_id") if live_master and isinstance(lease, dict) else None
        active_worker_ids = {
            row.get("worker_id")
            for row in state.get("active_workers", [])
            if isinstance(row, dict) and str(row.get("worker_id") or "").strip()
        }

        superseded = []
        recovery = []
        resume = []
        worker_events = []
        masters = []
        workers = []

        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("TURN_SCHEDULER_CANDIDATE_INVALID")
            turn = candidate.get("turn")
            if not isinstance(turn, dict):
                raise ValueError("TURN_SCHEDULER_TURN_INVALID")
            turn_id = str(candidate.get("turn_id") or "").strip()
            if not turn_id or turn.get("turn_id") != turn_id:
                raise ValueError("TURN_SCHEDULER_IDENTITY_INVALID")
            if turn.get("project_id") != state.get("project_id"):
                raise ValueError("TURN_SCHEDULER_PROJECT_MISMATCH")
            if turn.get("root_objective_sha256") != state.get("goal_contract_sha256"):
                raise ValueError("TURN_SCHEDULER_GOAL_MISMATCH")
            if turn.get("acceptance_sha256") != state.get("acceptance_sha256"):
                raise ValueError("TURN_SCHEDULER_ACCEPTANCE_MISMATCH")

            try:
                turn_version = int(turn.get("state_version"))
            except Exception as exc:
                raise ValueError("TURN_SCHEDULER_STATE_VERSION_INVALID") from exc
            state_version = int(state["state_version"])
            actor_kind = str(turn.get("actor_kind") or "").upper()

            if actor_kind == "MASTER":
                if turn_version < state_version:
                    if candidate.get("replayable") and turn_version + 1 == state_version:
                        recovery.append(candidate)
                        continue
                    superseded.append(self._superseded(candidate, "MASTER_STATE_VERSION_STALE"))
                    continue
                if turn_version > state_version:
                    raise ValueError("MASTER_STATE_VERSION_FUTURE")
                if terminal:
                    superseded.append(self._superseded(candidate, "PROJECT_TERMINAL"))
                    continue
                if not live_master or turn.get("session_id") != active_session:
                    superseded.append(self._superseded(candidate, "MASTER_SESSION_STALE"))
                    continue
                if turn.get("actor_id") != "A":
                    raise ValueError("MASTER_IDENTITY_INVALID")
                event = str((turn.get("payload") or {}).get("event") or "").upper()
                if event == "RESUME_MASTER":
                    resume.append(candidate)
                elif event in {"WORKER_HANDOFF", "WORKER_BLOCKER"}:
                    worker_events.append(candidate)
                else:
                    masters.append(candidate)
                continue

            if actor_kind == "WORKER":
                if terminal:
                    superseded.append(self._superseded(candidate, "PROJECT_TERMINAL"))
                    continue
                if turn_version > state_version:
                    raise ValueError("WORKER_STATE_VERSION_FUTURE")
                worker_id = str(turn.get("actor_id") or "")
                if worker_id not in active_worker_ids:
                    superseded.append(self._superseded(candidate, "WORKER_NOT_ACTIVE"))
                    continue
                workers.append(candidate)
                continue

            raise ValueError("ACTOR_KIND_INVALID")

        key = lambda row: row["turn_id"]
        recovery.sort(key=key)
        resume.sort(key=key)
        worker_events.sort(key=key)
        masters.sort(key=key)
        workers.sort(key=key)

        selected = None
        if recovery:
            selected = recovery[0]
        elif resume:
            selected = resume[0]
        elif worker_events:
            selected = worker_events[0]
        elif masters and workers:
            last_actor = self._last_routed_actor(ledger)
            selected = workers[0] if last_actor == "MASTER" else masters[0]
        elif masters:
            selected = masters[0]
        elif workers:
            selected = workers[0]

        return {
            "selected": selected,
            "superseded": sorted(superseded, key=lambda row: row["turn_id"]),
        }
