"""Fail-closed bootstrap for the single canonical SCORP V4 production authority."""

from __future__ import annotations

import argparse
import json
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from .install_manifest import (
    InstallIdentityError,
    _inside,
    verify_runtime_release,
)
from .models import canonical_json, sha256_json
from .state_store import StateStore, StoreInvariantError, utc_now


AUTHORITY_FORMAT = "scorp-v4-production-authority/1"
DEFAULT_ACCEPTANCE_ID = "AC_PERSISTENT_RUNTIME_OPERATIONAL"


def _atomic_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(canonical_json(dict(value)) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def verify_production_authority(
    authority_root: str | pathlib.Path,
    *,
    release_root: str | pathlib.Path,
    manifest: Mapping[str, Any],
    expected_project_id: str,
) -> dict[str, Any]:
    authority = pathlib.Path(authority_root).resolve(strict=True)
    release = pathlib.Path(release_root).resolve(strict=True)
    receipt_path = authority / "authority-receipt.json"
    if not receipt_path.is_file():
        raise InstallIdentityError("AUTHORITY_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if str(receipt.get("format") or "") != AUTHORITY_FORMAT:
        raise InstallIdentityError("AUTHORITY_RECEIPT_FORMAT_INVALID")
    project = str(expected_project_id or "").strip()
    if str(receipt.get("project_id") or "") != project:
        raise InstallIdentityError("AUTHORITY_PROJECT_MISMATCH")
    if str(receipt.get("release_root") or "") != str(release):
        raise InstallIdentityError("AUTHORITY_RELEASE_ROOT_MISMATCH")
    if str(receipt.get("candidate_commit") or "") != str(manifest.get("candidate_commit") or ""):
        raise InstallIdentityError("AUTHORITY_CANDIDATE_MISMATCH")
    if str(receipt.get("manifest_sha256") or "") != str(manifest.get("manifest_sha256") or ""):
        raise InstallIdentityError("AUTHORITY_MANIFEST_MISMATCH")
    release_receipt = json.loads((release / "release-receipt.json").read_text(encoding="utf-8"))
    verify_runtime_release(release, release_receipt, manifest)

    database = (authority / "state.sqlite3").resolve(strict=True)
    workspace = (authority / "workspace").resolve(strict=True)
    if str(receipt.get("database") or "") != str(database):
        raise InstallIdentityError("AUTHORITY_DATABASE_MISMATCH")
    if str(receipt.get("workspace") or "") != str(workspace):
        raise InstallIdentityError("AUTHORITY_WORKSPACE_MISMATCH")
    store = StateStore(database, [workspace])
    try:
        state = store.get_project_state(project)
        with store._connection() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM project_state").fetchone()[0])
            contract = conn.execute(
                "SELECT contract_sha256 FROM contracts WHERE project_id=?", (project,)
            ).fetchone()
        if count != 1:
            raise InstallIdentityError("AUTHORITY_PROJECT_COUNT_INVALID")
        if contract is None or str(receipt.get("contract_sha256") or "") != str(contract[0]):
            raise InstallIdentityError("AUTHORITY_CONTRACT_MISMATCH")
        return {
            "project_id": project,
            "status": str(state["status"]),
            "phase": str(state["phase"]),
            "state_version": int(state["state_version"]),
            "master_epoch": int(state["master_epoch"]),
            "candidate_commit": str(receipt["candidate_commit"]),
            "manifest_sha256": str(receipt["manifest_sha256"]),
            "database": str(database),
            "workspace": str(workspace),
        }
    finally:
        store.close()


def bootstrap_production_authority(
    authority_root: str | pathlib.Path,
    *,
    release_root: str | pathlib.Path,
    manifest: Mapping[str, Any],
    project_id: str,
    objective: str,
    acceptance_ids: Sequence[str] = (DEFAULT_ACCEPTANCE_ID,),
) -> dict[str, Any]:
    """Create exactly one new canonical production DB; never guess or migrate a lab DB."""

    authority = pathlib.Path(authority_root).resolve(strict=False)
    release = pathlib.Path(release_root).resolve(strict=True)
    project = str(project_id or "").strip()
    goal = str(objective or "").strip()
    required = [str(item).strip() for item in acceptance_ids if str(item).strip()]
    if not project:
        raise InstallIdentityError("AUTHORITY_PROJECT_ID_REQUIRED")
    if not goal:
        raise InstallIdentityError("AUTHORITY_OBJECTIVE_REQUIRED")
    if not required:
        raise InstallIdentityError("AUTHORITY_ACCEPTANCE_REQUIRED")
    if _inside(authority, release) or _inside(release, authority):
        raise InstallIdentityError("AUTHORITY_RELEASE_OVERLAP")
    release_receipt = json.loads((release / "release-receipt.json").read_text(encoding="utf-8"))
    verify_runtime_release(release, release_receipt, manifest)

    objective_sha = sha256_json(
        {
            "project_id": project,
            "objective": goal,
            "candidate_commit": str(manifest["candidate_commit"]),
            "manifest_sha256": str(manifest["manifest_sha256"]),
        }
    )
    receipt_path = authority / "authority-receipt.json"
    if receipt_path.exists():
        stored = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            str(stored.get("objective_sha256") or "") != objective_sha
            or list(stored.get("acceptance_ids") or []) != required
        ):
            raise InstallIdentityError("AUTHORITY_CONTRACT_REQUEST_MISMATCH")
        verify_production_authority(
            authority,
            release_root=release,
            manifest=manifest,
            expected_project_id=project,
        )
        return stored
    if authority.exists() and any(authority.iterdir()):
        raise InstallIdentityError("AUTHORITY_ROOT_NOT_EMPTY")

    authority.mkdir(parents=True, exist_ok=True)
    workspace = authority / "workspace"
    workspace.mkdir()
    database = authority / "state.sqlite3"
    driver_state = authority / "driver.json"
    health_path = authority / "daemon-health.json"
    root_contract = {
        "project_id": project,
        "root_objective": goal,
        "objective_sha256": objective_sha,
        "repository_baseline": str(manifest["candidate_commit"]),
        "runtime_manifest_sha256": str(manifest["manifest_sha256"]),
        "permitted_resources": [str(workspace.resolve())],
        "stop_conditions": [
            "AUTHENTICATION_REQUIRED",
            "CAPTCHA_REQUIRED",
            "UNCERTAIN_BROWSER_SUBMISSION",
            "OPERATOR_EMERGENCY_STOP",
        ],
    }
    acceptance_contract = {"required": required}

    store = StateStore(database, [workspace])
    try:
        contract = store.create_contract(
            project,
            root_contract=root_contract,
            acceptance_contract=acceptance_contract,
        )
        state = store.get_project_state(project)
        with store._connection() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM project_state").fetchone()[0])
        if count != 1 or str(state["status"]) != "ACTIVE":
            raise StoreInvariantError("AUTHORITY_BOOTSTRAP_STATE_INVALID")
    finally:
        store.close()

    receipt = {
        "format": AUTHORITY_FORMAT,
        "project_id": project,
        "candidate_commit": str(manifest["candidate_commit"]),
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "release_root": str(release),
        "database": str(database.resolve()),
        "workspace": str(workspace.resolve()),
        "driver_state": str(driver_state.resolve()),
        "health_path": str(health_path.resolve()),
        "contract_sha256": str(contract["contract_sha256"]),
        "objective_sha256": objective_sha,
        "acceptance_ids": required,
        "created_at": utc_now(),
    }
    _atomic_json(receipt_path, receipt)
    verify_production_authority(
        authority,
        release_root=release,
        manifest=manifest,
        expected_project_id=project,
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the canonical SCORP V4 production authority")
    parser.add_argument("--authority-root", required=True)
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--acceptance-id", action="append", default=[])
    args = parser.parse_args(argv)
    manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
    receipt = bootstrap_production_authority(
        args.authority_root,
        release_root=args.release_root,
        manifest=manifest,
        project_id=args.project_id,
        objective=args.objective,
        acceptance_ids=args.acceptance_id or [DEFAULT_ACCEPTANCE_ID],
    )
    print(canonical_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
