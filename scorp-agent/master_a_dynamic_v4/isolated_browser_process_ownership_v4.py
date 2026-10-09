"""Experimental safe browser-process ownership checks.

A previous failed isolated Chrome Use --launch was followed by 21 additional
Chrome root processes; 20 now have no live parent and no exact namespace tag.
Created-after timestamps, user-data-dir divergence and process names cannot
identify an owner. Never kill from these observations.

This module is *pure*: it does not inspect PIDs, launch Chrome, issue signatures,
open browsers, terminate processes, read secrets, or authorize cleanup.
A signed host witness would be needed to mark future process identities as
reviewable. Even a valid witness does NOT authorize termination.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import hmac
import json
import re


_SHA = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_NAMESPACE = re.compile(r"^scorp-r2-worker2-[a-z0-9-]{12,80}$")
_SIGNATURE_PROTOCOL = "scorp.r2.host-owned-chrome-process/1"


@dataclass(frozen=True)
class ChromeResourceSnapshot:
    chrome_process_total: int
    chrome_root_total: int
    chrome_roots_since_launch: int
    distinct_new_user_data_dirs: int
    roots_with_parent_missing: int
    roots_with_exact_worker_nonce: int
    free_physical_memory_mb: int
    chrome_working_set_mb: int


@dataclass(frozen=True)
class ProcessOwnershipWitness:
    protocol: str
    attempt_id: str
    namespace: str
    process_id: int
    process_created_epoch_ms: int
    user_data_dir_sha256: str
    windows_session_id: int
    witness_issued_epoch_ms: int
    signature: str


@dataclass(frozen=True)
class ChromeProcessObservation:
    process_id: int
    process_created_epoch_ms: int
    user_data_dir_sha256: str
    windows_session_id: int


@dataclass(frozen=True)
class OwnershipGate:
    status: str
    reason: str
    host_process_identity_attested: bool = False
    cleanup_authorized: bool = False
    browser_launch_authorized: bool = False
    browser_send_authorized: bool = False
    browser_processes_terminated: int = 0


def _valid_nonnegative(value: object) -> bool:
    return type(value) is int and value >= 0


def assess_residual_chrome_resources(snapshot: object) -> OwnershipGate:
    """Classify aggregate after-launch risk, never authorize cleanup."""
    if not isinstance(snapshot, ChromeResourceSnapshot):
        return OwnershipGate("BLOCKED", "RESOURCE_SNAPSHOT_UNTRUSTED")
    values = asdict(snapshot)
    if any(not _valid_nonnegative(v) for v in values.values()):
        return OwnershipGate("BLOCKED", "RESOURCE_COUNTER_INVALID")
    if (snapshot.chrome_root_total > snapshot.chrome_process_total
        or snapshot.chrome_roots_since_launch > snapshot.chrome_root_total
        or snapshot.distinct_new_user_data_dirs > snapshot.chrome_roots_since_launch
        or snapshot.roots_with_parent_missing > snapshot.chrome_roots_since_launch
        or snapshot.roots_with_exact_worker_nonce > snapshot.chrome_roots_since_launch):
        return OwnershipGate("BLOCKED", "RESOURCE_COUNTERS_INCONSISTENT")
    if snapshot.chrome_roots_since_launch > 0:
        return OwnershipGate(
            "HUMAN_ATTRIBUTION_REQUIRED", "POST_LAUNCH_CHROME_ROOTS_NOT_HOST_ATTESTED"
        )
    if snapshot.free_physical_memory_mb < 2048:
        return OwnershipGate("BLOCKED", "FREE_MEMORY_BELOW_ISOLATED_BROWSER_THRESHOLD")
    return OwnershipGate("NO_RESIDUAL_ROOTS_OBSERVED",
                         "NEITHER_PROFILE_OWNERSHIP_NOR_NEW_LAUNCH_AUTHORIZED")


def _canonical_witness(witness: ProcessOwnershipWitness) -> bytes:
    fields = asdict(witness)
    del fields["signature"]
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")


def review_host_process_witness(
    witness: object,
    observation: object,
    *,
    host_attestation_key: bytes | None,
    expected_attempt_id: str,
    expected_namespace: str,
) -> OwnershipGate:
    """Validate provenance of a future single root *for human review only*.

    A captured witness is only trustworthy if a real secured host signer issued
    it at process start with a private per-project key. There is NO such live
    signer attached to the failed October 9 launch.
    """
    def deny(reason: str) -> OwnershipGate:
        return OwnershipGate("BLOCKED", reason)
    if not isinstance(witness, ProcessOwnershipWitness):
        return deny("WITNESS_SCHEMA_INVALID")
    if not isinstance(observation, ChromeProcessObservation):
        return deny("PROCESS_OBSERVATION_SCHEMA_INVALID")
    if (not isinstance(host_attestation_key, bytes)
        or len(host_attestation_key) < 32):
        return deny("TRUSTED_HOST_ATTESTATION_KEY_MISSING")
    if (not isinstance(expected_attempt_id, str)
        or not _NONCE.fullmatch(expected_attempt_id)
        or not isinstance(expected_namespace, str)
        or not _NAMESPACE.fullmatch(expected_namespace)):
        return deny("EXPECTED_LAUNCH_BINDING_INVALID")
    if (witness.protocol != _SIGNATURE_PROTOCOL
        or witness.attempt_id != expected_attempt_id
        or witness.namespace != expected_namespace):
        return deny("WITNESS_ATTEMPT_SCOPE_MISMATCH")
    if (not isinstance(witness.signature, str)
        or _SHA.fullmatch(witness.signature) is None
        or not isinstance(witness.user_data_dir_sha256, str)
        or _SHA.fullmatch(witness.user_data_dir_sha256) is None):
        return deny("WITNESS_DIGEST_SCHEMA_INVALID")
    for value in (witness.process_id, witness.process_created_epoch_ms,
                  witness.windows_session_id, witness.witness_issued_epoch_ms,
                  observation.process_id, observation.process_created_epoch_ms,
                  observation.windows_session_id):
        if not _valid_nonnegative(value):
            return deny("PROCESS_IDENTITY_FIELD_INVALID")
    if witness.process_id == 0 or observation.process_id == 0:
        return deny("PROCESS_ID_INVALID")
    if (not isinstance(observation.user_data_dir_sha256, str)
        or _SHA.fullmatch(observation.user_data_dir_sha256) is None):
        return deny("OBSERVED_PROFILE_DIGEST_INVALID")
    proof = hmac.new(host_attestation_key, _canonical_witness(witness),
                     hashlib.sha256).hexdigest()
    if not hmac.compare_digest(proof, witness.signature):
        return deny("HOST_WITNESS_SIGNATURE_INVALID")
    if (witness.process_id != observation.process_id
        or witness.process_created_epoch_ms != observation.process_created_epoch_ms
        or witness.user_data_dir_sha256 != observation.user_data_dir_sha256
        or witness.windows_session_id != observation.windows_session_id):
        return deny("PROCESS_IDENTITY_OR_PROFILE_CHANGED")
    if (witness.witness_issued_epoch_ms > witness.process_created_epoch_ms
        or witness.process_created_epoch_ms - witness.witness_issued_epoch_ms > 180_000):
        return deny("WITNESS_NOT_ISSUED_AT_OR_BEFORE_LAUNCH")
    return OwnershipGate(
        "OWNERSHIP_EVIDENCE_FOR_HUMAN_REVIEW",
        "SIGNED_SINGLE_ROOT_IDENTITY_ONLY_NOT_PERMISSION_TO_TERMINATE",
        host_process_identity_attested=True,
    )
