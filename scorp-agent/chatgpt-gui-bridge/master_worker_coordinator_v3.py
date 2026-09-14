from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

UTC = dt.timezone.utc


class MasterWorkerCoordinatorV3:
    """Deterministic glue for D -> current A -> WorkerEventPump -> Relay.

    The coordinator does not reason or mutate PROJECT_STATE directly. D decides
    only whether an A window must exist; this class validates D's durable resume
    turn before acquiring the single operational master lease. Semantic state
    mutations remain exclusively in MasterStateTransitionV3 inside the relay.
    """

    def __init__(
        self,
        project_id,
        project_state_store,
        master_lease_store,
        watchdog,
        worker_event_pump,
        relay,
        outbox_dir,
        *,
        master_ttl_seconds=1500,
    ):
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("PROJECT_ID_EMPTY")
        self.project_state_store = project_state_store
        self.master_lease_store = master_lease_store
        self.watchdog = watchdog
        self.worker_event_pump = worker_event_pump
        self.relay = relay
        self.outbox = Path(outbox_dir)
        self.master_ttl_seconds = int(master_ttl_seconds)
        if self.master_ttl_seconds <= 0:
            raise ValueError("MASTER_LEASE_TTL_INVALID")

    def _now(self, value):
        value = value or dt.datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("COORDINATOR_NOW_MUST_BE_AWARE")
        return value.astimezone(UTC)

    def _validated_resume_turn(self, watchdog_result: dict, state: dict) -> dict:
        turn_id = str(watchdog_result.get("turn_id") or "").strip()
        session_id = str(watchdog_result.get("session_id") or "").strip()
        if not turn_id or not session_id:
            raise ValueError("COORDINATOR_RESUME_IDENTITY_MISSING")
        path = self.outbox / f"{turn_id}.json"
        if not path.is_file():
            raise ValueError("COORDINATOR_RESUME_TURN_MISSING")
        turn = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(turn, dict):
            raise ValueError("COORDINATOR_RESUME_TURN_INVALID")
        if turn.get("protocol_version") != "scorp.master-worker/turn-v3":
            raise ValueError("COORDINATOR_RESUME_PROTOCOL_INVALID")
        if turn.get("turn_id") != turn_id or turn.get("session_id") != session_id:
            raise ValueError("COORDINATOR_RESUME_IDENTITY_CONFLICT")
        if turn.get("project_id") != self.project_id or state.get("project_id") != self.project_id:
            raise ValueError("COORDINATOR_PROJECT_MISMATCH")
        if turn.get("actor_kind") != "MASTER" or turn.get("actor_id") != "A":
            raise ValueError("COORDINATOR_RESUME_ACTOR_INVALID")
        if (turn.get("payload") or {}).get("event") != "RESUME_MASTER":
            raise ValueError("COORDINATOR_RESUME_EVENT_INVALID")
        if turn.get("state_version") != state.get("state_version"):
            raise ValueError("COORDINATOR_RESUME_STATE_VERSION_CONFLICT")
        if turn.get("root_objective_sha256") != state.get("goal_contract_sha256"):
            raise ValueError("COORDINATOR_RESUME_GOAL_MISMATCH")
        if turn.get("acceptance_sha256") != state.get("acceptance_sha256"):
            raise ValueError("COORDINATOR_RESUME_ACCEPTANCE_MISMATCH")
        return turn

    def _activate_resume(self, watchdog_result: dict, state: dict, now: dt.datetime):
        turn = self._validated_resume_turn(watchdog_result, state)
        if self.master_lease_store.is_active(self.project_id, now=now):
            current = self.master_lease_store.load(self.project_id)
            if current and current.get("session_id") == turn["session_id"]:
                return current
            raise ValueError("COORDINATOR_MASTER_RACE")
        return self.master_lease_store.acquire(
            self.project_id,
            turn["session_id"],
            turn["turn_id"],
            turn["state_version"],
            now=now,
            ttl_seconds=self.master_ttl_seconds,
        )

    async def _run_relay(self, now: dt.datetime) -> dict:
        run_tick = getattr(self.relay, "run_tick", None)
        if callable(run_tick):
            return await run_tick(now=now)
        run_once = getattr(self.relay, "run_once", None)
        if callable(run_once):
            return await run_once(now=now)
        raise ValueError("COORDINATOR_RELAY_RUNNER_MISSING")

    async def run_once(self, *, now=None) -> dict:
        now = self._now(now)
        state = self.project_state_store.load()
        if state.get("project_id") != self.project_id:
            raise ValueError("COORDINATOR_PROJECT_MISMATCH")
        status = state.get("status")
        if status in {"COMPLETE", "HARD_BLOCKED"}:
            return {"status": status, "project_id": self.project_id}
        if status != "ACTIVE":
            raise ValueError("PROJECT_STATE_STATUS_INVALID")

        watchdog_result = self.watchdog.run_once(now=now)
        activation = None
        if watchdog_result.get("status") in {"QUEUED", "ALREADY_QUEUED"}:
            activation = self._activate_resume(watchdog_result, state, now)
        elif watchdog_result.get("status") != "MASTER_ACTIVE":
            raise ValueError("COORDINATOR_WATCHDOG_RESULT_INVALID")

        pump_result = self.worker_event_pump.run_once(self.project_id, now=now)
        relay_result = await self._run_relay(now)
        return {
            "status": "TICK",
            "project_id": self.project_id,
            "watchdog": watchdog_result,
            "activation": activation,
            "worker_event_pump": pump_result,
            "relay": relay_result,
        }
