"""Immutable GitHub artifact content proof (isolated R2, NO SEND).

Checks actual file bytes from one pinned Git commit through local gh auth.
This attests artifact integrity, NOT a ChatGPT Worker identity or final
turn event. It never enables browser send, worker wake, or local execution.
"""
from __future__ import annotations
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import shutil
import sys

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PATH = re.compile(r"^scorp-agent/r2-work-artifacts/[a-z0-9-]{4,56}\.json$")
_IDENT = re.compile(r"^(?:project|assignment|task)-[a-z0-9-]{4,80}$")
_MAX_BYTES = 32_768

@dataclass(frozen=True)
class ImmutableArtifactProof:
    status: str
    reason: str
    content_digest_verified: bool = False
    git_blob_identity_verified: bool = False
    scope_verified: bool = False
    substantive_work_product_present: bool = False
    work_product_sha256: str | None = None
    actual_worker_identity_verified: bool = False
    host_terminal_event_attested: bool = False
    browser_send_authorized: bool = False
    wake_master_authorized: bool = False
    wake_worker_authorized: bool = False
    local_execution_authorized: bool = False

def _native_gh_pinned_file(path: str, commit: str) -> object | None:
    if (not isinstance(path, str) or not _PATH.fullmatch(path)
        or not isinstance(commit, str) or not _COMMIT.fullmatch(commit)):
        return None
    gh = shutil.which("gh")
    if not isinstance(gh, str) or Path(gh).name.casefold() not in ("gh", "gh.exe"):
        return None
    endpoint = "repos/Scorp96/6/contents/" + path + "?ref=" + commit
    try:
        with tempfile.TemporaryFile(mode="w+b") as output, tempfile.TemporaryFile(mode="w+b") as errors:
            options = {"stdin": subprocess.DEVNULL, "stdout": output, "stderr": errors}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(
                [gh, "api", "--hostname", "github.com", endpoint], **options,
            )
            try:
                code = proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                return None
            if code != 0:
                return None
            output.seek(0)
            data = output.read(90_001)
            if len(data) > 90_000:
                return None
            value = json.loads(data.decode("utf-8"))
            return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError, UnicodeError):
        return None

def verify_immutable_github_artifact_for_review(
    *, path: str, commit_sha: str, expected_artifact_sha256: str,
    expected_project_id: str, expected_assignment_id: str,
    expected_task_id: str, expected_worker_slot: str,
    expected_state_version: int, reader=None,
    require_substantive_work_product: bool = False,
) -> ImmutableArtifactProof:
    def answer(status, reason, *, verified=False, substantive=False, digest=None):
        return ImmutableArtifactProof(
            status, reason,
            content_digest_verified=verified,
            git_blob_identity_verified=verified,
            scope_verified=verified,
            substantive_work_product_present=substantive,
            work_product_sha256=digest,
        )
    if type(require_substantive_work_product) is not bool:
        return answer("BLOCKED", "SUBSTANTIVE_REQUIREMENT_INVALID")
    if (not isinstance(path, str) or not _PATH.fullmatch(path)
        or not isinstance(commit_sha, str) or not _COMMIT.fullmatch(commit_sha)
        or not isinstance(expected_artifact_sha256, str)
        or not _SHA256.fullmatch(expected_artifact_sha256)
        or not all(isinstance(i, str) and _IDENT.fullmatch(i) for i in
                   (expected_project_id, expected_assignment_id, expected_task_id))
        or expected_worker_slot not in ("worker-slot-1", "worker-slot-2")
        or type(expected_state_version) is not int or expected_state_version < 0):
        return answer("BLOCKED", "PINNED_ARTIFACT_CONTRACT_INVALID")
    try:
        response = (reader or _native_gh_pinned_file)(path, commit_sha)
    except Exception:
        return answer("BLOCKED", "PINNED_ARTIFACT_GET_FAILED")
    if not isinstance(response, dict):
        return answer("BLOCKED", "PINNED_ARTIFACT_UNAVAILABLE")
    size, blob_sha = response.get("size"), response.get("sha")
    if (response.get("type") != "file" or response.get("path") != path
        or response.get("encoding") != "base64"
        or type(size) is not int or size <= 0 or size > _MAX_BYTES
        or not isinstance(blob_sha, str) or _COMMIT.fullmatch(blob_sha) is None):
        return answer("BLOCKED", "PINNED_ARTIFACT_GITHUB_METADATA_INVALID")
    try:
        content = response.get("content")
        if not isinstance(content, str) or len(content) > 50_000:
            return answer("BLOCKED", "PINNED_ARTIFACT_CONTENT_INVALID")
        raw = base64.b64decode(content, validate=False)
        if len(raw) != size or len(raw) > _MAX_BYTES:
            return answer("BLOCKED", "PINNED_ARTIFACT_SIZE_MISMATCH")
        if "".join(content.split()) != base64.b64encode(raw).decode("ascii"):
            return answer("BLOCKED", "PINNED_ARTIFACT_BASE64_INVALID")
        git_id = hashlib.sha1(
            ("blob " + str(len(raw)) + "\0").encode("ascii") + raw
        ).hexdigest()
        if git_id != blob_sha:
            return answer("BLOCKED", "PINNED_ARTIFACT_BLOB_ID_MISMATCH")
        if hashlib.sha256(raw).hexdigest() != expected_artifact_sha256:
            return answer("BLOCKED", "PINNED_ARTIFACT_SHA256_MISMATCH")
        artifact = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, binascii.Error, UnicodeError, OverflowError):
        return answer("BLOCKED", "PINNED_ARTIFACT_BODY_INVALID")
    base_keys = {"protocol", "project_id", "assignment_id", "task_id",
                 "worker_slot", "state_version", "kind", "evidence_status"}
    if not isinstance(artifact, dict):
        return answer("BLOCKED", "PINNED_ARTIFACT_SCHEMA_INVALID")
    is_v2 = artifact.get("protocol") == "scorp.r2.immutable-work-artifact/2"
    expected_keys = base_keys | ({"work_product"} if is_v2 else set())
    if set(artifact) != expected_keys:
        return answer("BLOCKED", "PINNED_ARTIFACT_SCHEMA_INVALID")
    if (artifact.get("protocol") not in (
            "scorp.r2.immutable-work-artifact/1",
            "scorp.r2.immutable-work-artifact/2")
        or artifact.get("kind") != "WORK_PRODUCT_FOR_REVIEW"
        or artifact.get("evidence_status") != "ARTIFACT_PRESENT_NOT_GPT_FINAL"
        or artifact.get("project_id") != expected_project_id
        or artifact.get("assignment_id") != expected_assignment_id
        or artifact.get("task_id") != expected_task_id
        or artifact.get("worker_slot") != expected_worker_slot
        or type(artifact.get("state_version")) is not int
        or artifact["state_version"] != expected_state_version):
        return answer("BLOCKED", "PINNED_ARTIFACT_ASSIGNMENT_SCOPE_INVALID")
    if not is_v2:
        if require_substantive_work_product:
            return answer("BLOCKED", "SUBSTANTIVE_WORK_PRODUCT_V2_REQUIRED")
        return answer("IMMUTABLE_ARTIFACT_VERIFIED_FOR_REVIEW",
                      "GIT_BLOB_AND_SHA256_MATCH_NO_GPT_TERMINAL_PROOF",
                      verified=True)
    work = artifact.get("work_product")
    if not isinstance(work, dict) or set(work) != {
        "title", "deliverable_markdown", "review_checks", "source_refs"
    }:
        return answer("BLOCKED", "WORK_PRODUCT_FIELDS_INVALID")
    title = work["title"]
    deliverable = work["deliverable_markdown"]
    checks = work["review_checks"]
    refs = work["source_refs"]
    if (not isinstance(title, str) or not 12 <= len(title) <= 160
        or title.isspace()
        or not isinstance(deliverable, str)
        or not 300 <= len(deliverable) <= 24000
        or len(deliverable.strip()) < 300
        or not isinstance(checks, list) or not 2 <= len(checks) <= 12
        or any(not isinstance(i, str) or not 12 <= len(i) <= 400
               or not i.strip() for i in checks)
        or not isinstance(refs, list) or len(refs) > 15
        or any(not isinstance(i, str) or not 8 <= len(i) <= 500
               or not i.strip() for i in refs)):
        return answer("BLOCKED", "SUBSTANTIVE_WORK_PRODUCT_INVALID")
    # Data presence is NOT work quality, model identity, independent
    # session provenance or the ChatGPT host TURN_FINAL event.
    work_sha = hashlib.sha256(deliverable.encode("utf-8")).hexdigest()
    return answer("SUBSTANTIVE_WORK_PRODUCT_FOR_HUMAN_REVIEW",
                  "PINNED_SUBSTANTIVE_TEXT_PRESENT_NO_GPT_IDENTITY",
                  verified=True, substantive=True, digest=work_sha)
