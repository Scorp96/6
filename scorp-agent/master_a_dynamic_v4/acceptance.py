from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from .evidence import evidence_blockers
from .models import AcceptanceStatus, sha256_json
from .state_store import StateStore


@dataclasses.dataclass(frozen=True)
class AcceptanceDecision:
    status: AcceptanceStatus
    blockers: tuple[str, ...]


class AcceptanceValidator:
    """Read-only completion validator; it deliberately has no PASS write method."""

    def __init__(self, store: StateStore):
        self.store = store

    def evaluate(
        self,
        project_id: str,
        candidate_commit: str,
        artifact_hashes: Mapping[str, str],
    ) -> AcceptanceDecision:
        blockers: set[str] = set()
        failed = False
        candidate = str(candidate_commit or "").strip().lower()
        authoritative_artifacts: set[str] = set()
        _ = artifact_hashes  # retained for call-site compatibility; never an authority source
        with self.store._connection() as conn:
            contract = conn.execute(
                "SELECT * FROM contracts WHERE project_id=?", (project_id,)
            ).fetchone()
            release = conn.execute(
                "SELECT * FROM release_candidates WHERE project_id=?", (project_id,)
            ).fetchone()
            state = conn.execute(
                "SELECT * FROM project_state WHERE project_id=?", (project_id,)
            ).fetchone()
            if contract is None or state is None:
                return AcceptanceDecision(AcceptanceStatus.BLOCKED, ("PROJECT_OR_CONTRACT_MISSING",))
            if release is None:
                blockers.add("RELEASE_CANDIDATE_MISSING")
            else:
                if str(release["candidate_commit"]) != candidate:
                    blockers.add("RELEASE_CANDIDATE_MISMATCH")
                if str(state["completion_candidate_commit"] or "") != candidate:
                    blockers.add("PROJECT_CANDIDATE_MISMATCH")
                try:
                    manifest = json.loads(str(release["manifest_json"]))
                except (TypeError, ValueError):
                    manifest = None
                if not isinstance(manifest, dict) or not manifest:
                    blockers.add("RELEASE_MANIFEST_INVALID")
                elif str(release["manifest_sha256"]) != sha256_json(manifest):
                    blockers.add("RELEASE_MANIFEST_HASH_MISMATCH")
                else:
                    files = manifest.get("files")
                    if not isinstance(files, dict) or not files:
                        blockers.add("RELEASE_MANIFEST_INVALID")
                    else:
                        manifest_invalid = False
                        for raw_hash in files.values():
                            digest = str(raw_hash or "").strip().lower()
                            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                                manifest_invalid = True
                                break
                            authoritative_artifacts.add(digest)
                        if manifest_invalid:
                            authoritative_artifacts.clear()
                            blockers.add("RELEASE_MANIFEST_INVALID")

            try:
                acceptance_contract = json.loads(str(contract["acceptance_contract_json"]))
                required = acceptance_contract.get("required", [])
            except (TypeError, ValueError, AttributeError):
                required = []
                blockers.add("ACCEPTANCE_CONTRACT_INVALID")
            if not isinstance(required, list) or not required:
                blockers.add("ACCEPTANCE_REQUIREMENTS_EMPTY")
                required = []

            receipts = conn.execute(
                "SELECT * FROM evidence_receipts WHERE project_id=? ORDER BY acceptance_id,evidence_ref",
                (project_id,),
            ).fetchall()
            by_acceptance: dict[str, list[Any]] = {}
            for receipt in receipts:
                by_acceptance.setdefault(str(receipt["acceptance_id"]), []).append(receipt)
            for acceptance_id in required:
                acceptance_name = str(acceptance_id)
                candidates = by_acceptance.get(acceptance_name, [])
                if not candidates:
                    blockers.add(f"MISSING_EVIDENCE:{acceptance_name}")
                    continue
                receipt = candidates[-1]
                result = str(receipt["result"])
                if result == "FAIL":
                    failed = True
                    blockers.add(f"EVIDENCE_FAILED:{acceptance_name}")
                elif result != "PASS":
                    blockers.add(f"EVIDENCE_NOT_PASS:{acceptance_name}:{result}")
                blockers.update(evidence_blockers(
                    dict(receipt),
                    candidate_commit=candidate,
                    contract_sha256=str(contract["contract_sha256"]),
                    artifact_hashes=authoritative_artifacts,
                ))

            required_tasks = conn.execute(
                "SELECT task_id,state FROM task_nodes WHERE project_id=? AND required=1 ORDER BY task_id",
                (project_id,),
            ).fetchall()
            for task in required_tasks:
                if str(task["state"]) != "VERIFIED":
                    blockers.add(f"REQUIRED_TASK_NOT_VERIFIED:{task['task_id']}")
            if conn.execute(
                "SELECT 1 FROM assignments WHERE project_id=? AND state IN ('ACTIVE','RESULT_RECEIVED') LIMIT 1",
                (project_id,),
            ).fetchone() is not None:
                blockers.add("ACTIVE_WORKERS")
            if conn.execute(
                "SELECT 1 FROM candidate_results WHERE project_id=? AND verification_state <> 'VERIFIED' LIMIT 1",
                (project_id,),
            ).fetchone() is not None:
                blockers.add("UNVERIFIED_CANDIDATE_RESULTS")
            if conn.execute(
                "SELECT 1 FROM outbox WHERE project_id=? AND state <> 'COMPLETED' LIMIT 1",
                (project_id,),
            ).fetchone() is not None:
                blockers.add("OUTBOX_UNRESOLVED")
            if conn.execute(
                "SELECT 1 FROM action_intents WHERE project_id=? AND state IN ('MAY_HAVE_SUBMITTED','BLOCKED_AMBIGUOUS') LIMIT 1",
                (project_id,),
            ).fetchone() is not None:
                blockers.add("BROWSER_INTENTS_UNRESOLVED")
            if conn.execute(
                "SELECT 1 FROM review_findings WHERE project_id=? AND blocking=1 AND resolved_at IS NULL LIMIT 1",
                (project_id,),
            ).fetchone() is not None:
                blockers.add("BLOCKING_FINDINGS")
            if conn.execute(
                "SELECT 1 FROM review_findings WHERE project_id=? AND blocking=1 AND late=1 AND resolved_at IS NULL LIMIT 1",
                (project_id,),
            ).fetchone() is not None:
                blockers.add("LATE_BLOCKING_FINDINGS")

        ordered = tuple(sorted(blockers))
        if failed:
            return AcceptanceDecision(AcceptanceStatus.FAIL, ordered)
        if ordered:
            return AcceptanceDecision(AcceptanceStatus.BLOCKED, ordered)
        return AcceptanceDecision(AcceptanceStatus.PASS, ())
