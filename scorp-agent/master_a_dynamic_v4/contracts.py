from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ALLOWED_PROPOSAL_KINDS = {
    "SET_PHASE",
    "TASK_GRAPH",
    "ASSIGNMENTS",
    "INTEGRATION",
    "RETRY",
    "REPLAN",
    "COMPLETION_REQUEST",
}


class ProposalRejected(ValueError):
    pass


def validate_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProposalRejected("PROPOSAL_NOT_MAPPING")
    proposal = dict(value)
    project_id = str(proposal.get("project_id") or "").strip()
    if not project_id:
        raise ProposalRejected("PROPOSAL_PROJECT_ID_MISSING")
    if str(proposal.get("master_identity") or "A") != "A":
        raise ProposalRejected("MASTER_IDENTITY_INVALID")
    kind = str(proposal.get("kind") or "").strip().upper()
    if kind not in ALLOWED_PROPOSAL_KINDS:
        raise ProposalRejected("PROPOSAL_KIND_INVALID")
    proposal["project_id"] = project_id
    proposal["kind"] = kind
    proposal["master_identity"] = "A"
    if kind == "SET_PHASE":
        phase = str(proposal.get("phase") or "").strip()
        if not phase:
            raise ProposalRejected("PROPOSAL_PHASE_MISSING")
        proposal["phase"] = phase
    return proposal
