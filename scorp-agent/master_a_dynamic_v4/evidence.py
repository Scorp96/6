from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def evidence_blockers(
    receipt: Mapping[str, Any],
    *,
    candidate_commit: str,
    contract_sha256: str,
    artifact_hashes: set[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    acceptance_id = str(receipt.get("acceptance_id") or "UNKNOWN")
    suffix = f":{acceptance_id}"
    if str(receipt.get("candidate_commit")) != candidate_commit:
        blockers.append("EVIDENCE_CANDIDATE_MISMATCH" + suffix)
    if str(receipt.get("contract_sha256")) != contract_sha256:
        blockers.append("EVIDENCE_CONTRACT_MISMATCH" + suffix)
    if str(receipt.get("artifact_sha256")) not in artifact_hashes:
        blockers.append("EVIDENCE_ARTIFACT_MISMATCH" + suffix)
    if not str(receipt.get("command_or_action") or "").strip():
        blockers.append("EVIDENCE_ACTION_EMPTY" + suffix)
    if not str(receipt.get("raw_output_reference") or "").strip():
        blockers.append("EVIDENCE_RAW_OUTPUT_EMPTY" + suffix)
    try:
        observed = json.loads(str(receipt.get("observed_state_json") or ""))
    except (TypeError, ValueError):
        observed = None
    if not isinstance(observed, dict) or not observed:
        blockers.append("EVIDENCE_OBSERVED_STATE_EMPTY" + suffix)
    elif observed == {"status": "PASS"}:
        blockers.append("SELF_ASSERTED_PASS" + suffix)
    return tuple(blockers)
