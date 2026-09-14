from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from project_state_v3 import ProjectStateStore


BOOTSTRAP_PROTOCOL = "scorp.project-bootstrap/v1"
CONTRACT_PROTOCOL = "scorp.project-contract/v1"


def sha256_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("CONTRACT_TEXT_INVALID")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_contract_text(value, error: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    return value


def _required_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("PROJECT_ID_EMPTY")
    return text


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


class ProjectBootstrapV3:
    """Create the immutable project contract and authoritative v0 state.

    Bootstrap deliberately does not create a master lease. Once the ACTIVE
    project-state exists, deterministic D emits RESUME_MASTER and the
    coordinator acquires the single operational A lease.
    """

    def __init__(self, project_root):
        self.root = Path(project_root)
        self.state_path = self.root / "project-state.json"
        self.contract_path = self.root / "project-contract.json"

    def create(self, *, project_id, root_objective, acceptance_criteria) -> dict:
        project = _required_id(project_id)
        objective = _required_contract_text(root_objective, "ROOT_OBJECTIVE_EMPTY")
        acceptance = _required_contract_text(acceptance_criteria, "ACCEPTANCE_CRITERIA_EMPTY")

        goal_hash = sha256_text(objective)
        acceptance_hash = sha256_text(acceptance)
        contract = {
            "protocol_version": CONTRACT_PROTOCOL,
            "project_id": project,
            "master_identity": "A",
            "root_objective": objective,
            "root_objective_sha256": goal_hash,
            "acceptance_criteria": acceptance,
            "acceptance_sha256": acceptance_hash,
        }
        initial_state = {
            "protocol_version": "scorp.project-state/v1",
            "project_id": project,
            "master_identity": "A",
            "state_version": 0,
            "goal_contract_sha256": goal_hash,
            "acceptance_sha256": acceptance_hash,
            "status": "ACTIVE",
            "current_phase": "BOOTSTRAPPED",
            "next_exact_action": "resume master A from root contract",
            "active_master_window": None,
            "active_workers": [],
            "completed": [],
            "blocked": [],
            "evidence": [],
            "commits": [],
            "tests": [],
        }

        self.root.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            raise ValueError("PROJECT_BOOTSTRAP_ALREADY_EXISTS")

        created_contract = False
        if self.contract_path.exists():
            try:
                existing_contract = json.loads(self.contract_path.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                raise ValueError("PROJECT_BOOTSTRAP_CONTRACT_CONFLICT") from exc
            if existing_contract != contract:
                raise ValueError("PROJECT_BOOTSTRAP_CONTRACT_CONFLICT")
        else:
            _atomic_json(self.contract_path, contract)
            existing_contract = contract
            created_contract = True

        try:
            state = ProjectStateStore(self.state_path).create(initial_state)
        except Exception:
            if created_contract and not self.state_path.exists() and self.contract_path.exists():
                self.contract_path.unlink()
            raise
        return {"contract": existing_contract, "state": state}


def create_from_spec(project_root, spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("PROJECT_BOOTSTRAP_SPEC_NOT_OBJECT")
    if spec.get("protocol_version") != BOOTSTRAP_PROTOCOL:
        raise ValueError("PROJECT_BOOTSTRAP_PROTOCOL_INVALID")
    return ProjectBootstrapV3(project_root).create(
        project_id=spec.get("project_id"),
        root_objective=spec.get("root_objective"),
        acceptance_criteria=spec.get("acceptance_criteria"),
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="Create Scorp Master/Worker V3 project lifecycle state")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()

    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    result = create_from_spec(args.project_root, spec)
    state = result["state"]
    print(
        json.dumps(
            {
                "status": "PROJECT_BOOTSTRAP_CREATED",
                "project_id": state["project_id"],
                "state_version": state["state_version"],
                "goal_contract_sha256": state["goal_contract_sha256"],
                "acceptance_sha256": state["acceptance_sha256"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
