"""Windows-local GitHub work-artifact reader for SCORP R2 (NO SEND).

The host's ALREADY AUTHENTICATED gh CLI can GET an exact private GitHub issue
comment without a GPT model call. An allowlisted, scope-pinned comment is
then staged into the isolated review-only SQLite ledger.

This is not a browser terminal issuer and never wakes ChatGPT, assigns
workers, mutates GitHub issues, or performs local task execution.
CLI stdout/stderr are bounded and NEVER surfaced in errors or repr.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .isolated_github_work_result_inbox_v4 import (
    _safe_db, stage_explicit_github_worker_artifact_for_review,
)


_REPO = "Scorp96/scorp-control-plane"


@dataclass(frozen=True)
class GhWorkArtifactIntake:
    status: str
    reason: str
    github_comment_read: bool = False
    review_row_committed: bool = False
    local_model_calls: int = 0
    chatgpt_turn_final_attested: bool = False
    browser_send_authorized: bool = False
    wake_master_authorized: bool = False
    wake_worker_authorized: bool = False
    production_write_authorized: bool = False


def _gh_get_comment(comment_id: int) -> object | None:
    """Only GH API GET to an exact pinned private repo issue-comment ID."""
    if type(comment_id) is not int or not 1 <= comment_id <= 10**20:
        return None
    gh = shutil.which("gh")
    if not gh or Path(gh).name.lower() not in ("gh", "gh.exe"):
        return None
    argv = [
        gh, "api", "--hostname", "github.com",
        "repos/" + _REPO + "/issues/comments/" + str(comment_id),
    ]
    # Subprocess PIPE can hang after a descendant inherits stdout. Capture
    # into temporary files instead and enforce wall-clock wait on the direct
    # process; never echo gh diagnostics or credential-bearing environment.
    env = dict(os.environ)
    env["GH_HOST"] = "github.com"
    env["GH_PAGER"] = "cat"
    try:
        with tempfile.TemporaryFile(mode="w+b") as out, tempfile.TemporaryFile(mode="w+b") as err:
            opts = {
                "stdin": subprocess.DEVNULL,
                "stdout": out, "stderr": err,
                "env": env,
            }
            if sys.platform == "win32":
                opts["creationflags"] = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(argv, **opts)
            try:
                code = process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                return None
            if code != 0:
                return None
            out.seek(0)
            raw = out.read(16_385)
            if len(raw) > 16_384:
                return None
            doc = json.loads(raw.decode("utf-8"))
            return doc if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def ingest_pinned_github_artifact_without_send(
    *,
    database: Path,
    allowed_experiments_root: Path,
    comment_id: int,
    expected_issue_number: int,
    allowlisted_github_author_id: int,
    expected_project_id: str,
    expected_assignment_id: str,
    expected_task_id: str,
    expected_worker_slot: str,
    expected_assignment_sha256: str,
    expected_state_version: int,
    reader=None,
) -> GhWorkArtifactIntake:
    """One pinned comment read and durable REVIEW-ONLY stage; never dispatch."""
    def result(status, reason, read=False, stored=False):
        return GhWorkArtifactIntake(
            status, reason,
            github_comment_read=read, review_row_committed=stored,
        )
    if not _safe_db(database, allowed_experiments_root):
        return result("BLOCKED", "ISOLATED_REVIEW_DB_REQUIRED")
    if (
        type(comment_id) is not int or not 1 <= comment_id <= 10**20
        or type(expected_issue_number) is not int or expected_issue_number < 1
        or type(allowlisted_github_author_id) is not int
        or allowlisted_github_author_id < 1
    ):
        return result("BLOCKED", "PINNED_GITHUB_COMMENT_ID_INVALID")

    try:
        fetched = (reader or _gh_get_comment)(comment_id)
    except Exception:
        return result("BLOCKED", "GH_READ_UNAVAILABLE")
    if not isinstance(fetched, dict) or fetched.get("id") != comment_id:
        return result("BLOCKED", "GH_COMMENT_NOT_VERIFIED")
    if fetched.get("url") != (
        "https://api.github.com/repos/" + _REPO
        + "/issues/comments/" + str(comment_id)
    ):
        return result("BLOCKED", "GH_REST_COMMENT_LINK_MISMATCH")

    decision = stage_explicit_github_worker_artifact_for_review(
        database=database,
        allowed_experiments_root=allowed_experiments_root,
        comment=fetched,
        verified_issue_number=expected_issue_number,
        allowlisted_github_author_id=allowlisted_github_author_id,
        expected_project_id=expected_project_id,
        expected_assignment_id=expected_assignment_id,
        expected_task_id=expected_task_id,
        expected_worker_slot=expected_worker_slot,
        expected_assignment_sha256=expected_assignment_sha256,
        expected_state_version=expected_state_version,
    )
    return result(
        "GITHUB_ARTIFACT_STAGED_REVIEW_ONLY"
        if decision.staged_for_review else "BLOCKED",
        "GH_RESULT_NOT_GPT_FINAL_EVENT"
        if decision.staged_for_review else decision.reason,
        read=True,
        stored=decision.staged_for_review,
    )
