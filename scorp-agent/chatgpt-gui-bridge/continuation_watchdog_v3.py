from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path


TURN_PROTOCOL = "scorp.master-worker/turn-v3"
ROLE_TURN_BUDGET_SECONDS = 1500
HANDOFF_RESERVE_SECONDS = 300


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


class ContinuationWatchdog:
    """Deterministic D role.

    D never reasons, replans, dispatches workers, or mutates PROJECT_STATE.
    It only observes authoritative semantic PROJECT_STATE plus the operational
    single-master lease and emits one deterministic V3 RESUME_MASTER turn when
    A is absent or its lease has ended. When production supplies the immutable
    project contract, D mechanically binds that already-validated text into the
    resume payload so a fresh A window receives the objective it is preserving.
    """

    def __init__(self, project_state_store, master_lease_store, outbox_dir, *, project_contract=None):
        self.project_state_store = project_state_store
        self.master_lease_store = master_lease_store
        self.outbox = Path(outbox_dir)
        self.project_contract = copy.deepcopy(project_contract) if project_contract is not None else None

    def _lease_status(self, project_id: str, now: dt.datetime):
        lease = self.master_lease_store.load(project_id)
        if lease and lease.get("status") == "ACTIVE":
            if self.master_lease_store.is_active(project_id, now=now):
                return True, "MASTER_ACTIVE", lease
            return False, "LEASE_EXPIRED", lease
        if lease:
            return False, "LEASE_ENDED", lease
        return False, "NO_ACTIVE_MASTER", None

    def _contract_context(self, state: dict):
        if self.project_contract is None:
            return None
        contract = self.project_contract
        if not isinstance(contract, dict):
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_INVALID")
        if contract.get("protocol_version") != "scorp.project-contract/v1":
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_PROTOCOL_INVALID")
        if contract.get("project_id") != state.get("project_id"):
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_PROJECT_MISMATCH")
        if contract.get("master_identity") != "A" or state.get("master_identity") != "A":
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_MASTER_INVALID")
        objective = contract.get("root_objective")
        acceptance = contract.get("acceptance_criteria")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_OBJECTIVE_INVALID")
        if not isinstance(acceptance, str) or not acceptance.strip():
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_ACCEPTANCE_INVALID")
        goal_hash = hashlib.sha256(objective.encode("utf-8")).hexdigest()
        acceptance_hash = hashlib.sha256(acceptance.encode("utf-8")).hexdigest()
        if (
            goal_hash != contract.get("root_objective_sha256")
            or goal_hash != state.get("goal_contract_sha256")
        ):
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_GOAL_MISMATCH")
        if (
            acceptance_hash != contract.get("acceptance_sha256")
            or acceptance_hash != state.get("acceptance_sha256")
        ):
            raise ValueError("WATCHDOG_PROJECT_CONTRACT_ACCEPTANCE_MISMATCH")
        return copy.deepcopy(contract)

    def _resume_turn(self, state: dict, reason: str, previous_lease) -> dict:
        state_version = int(state["state_version"])
        predecessor = None
        if isinstance(previous_lease, dict):
            predecessor = {
                "session_id": previous_lease.get("session_id"),
                "status": previous_lease.get("status"),
                "lease_until": previous_lease.get("lease_until"),
                "finished_at": previous_lease.get("finished_at"),
            }
        identity_material = {
            "project_id": state["project_id"],
            "state_version": state_version,
            "goal_contract_sha256": state["goal_contract_sha256"],
            "acceptance_sha256": state["acceptance_sha256"],
            "event": "RESUME_MASTER",
            "predecessor": predecessor,
        }
        identity_hash = _sha(identity_material)
        turn_id = "resume-a-" + identity_hash[:20]
        session_id = f"a-window-v{state_version}-" + identity_hash[20:32]
        payload = {
            "event": "RESUME_MASTER",
            "reason": reason,
            "project_state_version": state_version,
            "project_state_sha256": _sha(state),
            "project_state": state,
            "previous_master_lease": predecessor,
            "role_turn_budget_seconds": ROLE_TURN_BUDGET_SECONDS,
            "handoff_reserve_seconds": HANDOFF_RESERVE_SECONDS,
        }
        contract = self._contract_context(state)
        if contract is not None:
            payload["project_contract"] = contract
        return {
            "protocol_version": TURN_PROTOCOL,
            "turn_id": turn_id,
            "project_id": state["project_id"],
            "state_version": state_version,
            "actor_kind": "MASTER",
            "actor_id": "A",
            "session_id": session_id,
            "root_objective_sha256": state["goal_contract_sha256"],
            "acceptance_sha256": state["acceptance_sha256"],
            "payload": payload,
        }

    def run_once(self, now=None) -> dict:
        now = now or dt.datetime.now(dt.timezone.utc)
        if now.tzinfo is None:
            raise ValueError("WATCHDOG_NOW_MUST_BE_AWARE")
        now = now.astimezone(dt.timezone.utc)
        state = self.project_state_store.load()

        status = state["status"]
        if status in {"COMPLETE", "HARD_BLOCKED"}:
            return {"status": status, "project_id": state["project_id"]}
        if status != "ACTIVE":
            raise ValueError("PROJECT_STATE_STATUS_INVALID")

        active, reason, previous_lease = self._lease_status(state["project_id"], now)
        if active:
            return {
                "status": "MASTER_ACTIVE",
                "project_id": state["project_id"],
                "state_version": state["state_version"],
                "session_id": previous_lease.get("session_id"),
            }

        turn = self._resume_turn(state, reason, previous_lease)
        path = self.outbox / f"{turn['turn_id']}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
            if _sha(existing) != _sha(turn):
                raise ValueError("WATCHDOG_RESUME_CONFLICT")
            return {
                "status": "ALREADY_QUEUED",
                "reason": reason,
                "turn_id": turn["turn_id"],
                "session_id": turn["session_id"],
                "project_id": state["project_id"],
                "state_version": state["state_version"],
            }

        _atomic_json(path, turn)
        return {
            "status": "QUEUED",
            "reason": reason,
            "turn_id": turn["turn_id"],
            "session_id": turn["session_id"],
            "project_id": state["project_id"],
            "state_version": state["state_version"],
        }
