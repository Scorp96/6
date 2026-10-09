"""Side-effect-free GPT conversation admission policy for an isolated candidate.

A logical Master/Worker is NOT a GPT model label. This module validates
pre-observed local session evidence to decide whether a conversation is
eligible for scheduling. It does not authenticate the browser, call a model,
allocate a lease, submit a prompt, or grant execution authority.

The caller must obtain `SessionObservation` from a trusted local adapter, not
from an untrusted GPT response. Legacy model-exact operating rules are honored
when passed through `AdmissionPolicy.required_model`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


Role = Literal["MASTER", "WORKER"]


@dataclass(frozen=True)
class SessionObservation:
    session_id: str
    conversation_url: str
    role: Role
    generation: int
    auth_status: str
    physical_status: str
    response_status: str
    unresolved_intents: int = 0
    model_label: str | None = None
    model_verification: str = "UNVERIFIED"
    auth_verification: str = "UNVERIFIED"
    physical_verification: str = "UNVERIFIED"


@dataclass(frozen=True)
class AdmissionPolicy:
    project_status: str
    operator_status: str
    required_role: Role
    expected_generation: int
    expected_conversation_url: str
    active_workers: int = 0
    max_workers: int = 2
    required_model: str | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    status: str
    reason: str
    session_id: str
    role: str
    model_label: str | None
    model_verification: str
    browser_send_authorized: bool = False


def _canonical_conversation_url(raw: str) -> str | None:
    """Canonicalize only exact chatgpt.com /c/<id> conversation URLs."""
    try:
        value = urlsplit(str(raw or "").strip())
    except ValueError:
        return None
    if (
        value.scheme != "https"
        or value.netloc != "chatgpt.com"
        or value.username is not None
        or value.password is not None
        or value.query
        or value.fragment
    ):
        return None
    segments = value.path.split("/")
    if (
        len(segments) != 3
        or segments[0] != ""
        or segments[1] != "c"
        or not segments[2]
        or not all(ch.isalnum() or ch in "-_" for ch in segments[2])
    ):
        return None
    return f"https://chatgpt.com/c/{segments[2]}"


def evaluate_session(
    observation: SessionObservation,
    policy: AdmissionPolicy,
) -> AdmissionDecision:
    """Return scheduling eligibility only, never permission to send or execute.

    The policy is independent of model *names* unless the active operator
    explicitly requests a particular, independently host-verified model.
    The returned decision is not proof that the observation is trustworthy;
    adapters still need to attest physical browser and auth evidence.
    """
    if not isinstance(observation, SessionObservation):
        raise TypeError("SESSION_OBSERVATION_REQUIRED")
    if not isinstance(policy, AdmissionPolicy):
        raise TypeError("SESSION_POLICY_REQUIRED")

    def blocked(reason: str) -> AdmissionDecision:
        return AdmissionDecision(
            "BLOCKED", reason, observation.session_id, observation.role,
            observation.model_label, observation.model_verification,
        )

    if not observation.session_id.strip():
        return blocked("SESSION_ID_MISSING")
    if policy.project_status != "ACTIVE":
        return blocked("PROJECT_NOT_ACTIVE")
    if policy.operator_status not in {"ACTIVE", "RUNNING"}:
        return blocked("OPERATOR_NOT_RUNNING")
    if observation.role not in {"MASTER", "WORKER"}:
        return blocked("ROLE_UNKNOWN")
    if observation.role != policy.required_role:
        return blocked("ROLE_MISMATCH")
    if (
        type(observation.generation) is not int
        or type(policy.expected_generation) is not int
        or policy.expected_generation < 0
        or observation.generation != policy.expected_generation
    ):
        return blocked("GENERATION_MISMATCH")
    expected_url = _canonical_conversation_url(policy.expected_conversation_url)
    observed_url = _canonical_conversation_url(observation.conversation_url)
    if not expected_url or observed_url != expected_url:
        return blocked("CONVERSATION_BINDING_UNVERIFIED")
    if observation.auth_status != "AUTHENTICATED":
        return blocked("AUTHENTICATION_UNVERIFIED")
    if observation.auth_verification != "HOST_VERIFIED":
        return blocked("HOST_AUTH_PROOF_UNVERIFIED")
    if observation.physical_status != "VERIFIED":
        return blocked("PHYSICAL_SESSION_UNVERIFIED")
    if observation.physical_verification != "HOST_VERIFIED":
        return blocked("HOST_PHYSICAL_PROOF_UNVERIFIED")
    if type(observation.unresolved_intents) is not int or observation.unresolved_intents < 0:
        return blocked("UNRESOLVED_INTENT_COUNT_INVALID")
    if observation.unresolved_intents:
        return blocked("UNRESOLVED_BROWSER_INTENTS")
    if observation.response_status != "IDLE_CONFIRMED":
        return blocked("RESPONSE_NOT_IDLE_CONFIRMED")
    if (
        type(policy.active_workers) is not int
        or type(policy.max_workers) is not int
        or policy.active_workers < 0
        or policy.max_workers < 1
        or policy.max_workers > 2  # V4 release capacity, not the number of known GPT sessions
    ):
        return blocked("CAPACITY_POLICY_INVALID")
    if observation.role == "WORKER" and policy.active_workers >= policy.max_workers:
        return blocked("WORKER_CAPACITY_EXHAUSTED")
    if policy.required_model is not None:
        if not str(policy.required_model).strip():
            return blocked("REQUIRED_MODEL_INVALID")
        if observation.model_verification != "HOST_VERIFIED":
            return blocked("REQUIRED_MODEL_UNVERIFIED")
        if observation.model_label != policy.required_model:
            return blocked("REQUIRED_MODEL_MISMATCH")

    return AdmissionDecision(
        "ELIGIBLE_FOR_SCHEDULING",
        "CAPABILITY_AND_BINDING_CHECKS_PASSED",
        observation.session_id,
        observation.role,
        observation.model_label,
        observation.model_verification,
    )
