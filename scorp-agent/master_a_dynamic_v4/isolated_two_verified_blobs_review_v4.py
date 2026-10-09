"""Strict read-only two-worker immutable file evidence barrier.

Requires BOTH existing GitHub comment review rows AND additional blob
evidence receipts, each tied to exactly the expected Git commit/file path,
SHA256 and worker assignment. It never infers ChatGPT turn completion,
real Worker identity or permission to send/wake.
"""
from __future__ import annotations
import contextlib
from dataclasses import dataclass
import hashlib
import sqlite3

from .isolated_github_work_result_inbox_v4 import _safe_db
from .isolated_gh_immutable_artifact_v4 import _PATH,_COMMIT
from .isolated_two_worker_artifact_review_v4 import (
    inspect_two_worker_review_only,
)

@dataclass(frozen=True)
class TwoVerifiedArtifacts:
    status: str
    reason: str
    verified_artifact_rows: int = 0
    both_git_blobs_verified_for_human_review: bool = False
    actual_worker_identity_verified: bool = False
    host_terminal_event_attested: bool = False
    browser_send_authorized: bool = False
    wake_master_authorized: bool = False
    wake_worker_authorized: bool = False
    local_execution_authorized: bool = False

def inspect_two_git_verified_worker_results(
    *, barrier_kwargs: dict,
    first_commit_sha: str, first_artifact_path: str,
    second_commit_sha: str, second_artifact_path: str,
) -> TwoVerifiedArtifacts:
    def output(status,reason,count=0,ok=False):
        return TwoVerifiedArtifacts(status,reason,count,ok)
    if not isinstance(barrier_kwargs,dict):
        return output("BLOCKED","BARRIER_SCOPE_REQUIRED")
    for name in ("first_commit_sha","second_commit_sha"):
        v=first_commit_sha if name.startswith("first") else second_commit_sha
        if not isinstance(v,str) or _COMMIT.fullmatch(v) is None:
            return output("BLOCKED","PINNED_COMMIT_SCOPE_INVALID")
    for path in (first_artifact_path,second_artifact_path):
        if not isinstance(path,str) or _PATH.fullmatch(path) is None:
            return output("BLOCKED","PINNED_ARTIFACT_PATH_INVALID")
    if first_artifact_path == second_artifact_path:
        return output("BLOCKED","WORKER_ARTIFACT_PATH_REUSED")
    required={
        "database","allowed_experiments_root",
        "expected_project_id","expected_state_version",
        "worker_1_assignment_id","worker_1_assignment_sha256",
        "worker_1_issue_number","worker_1_author_id",
        "worker_2_assignment_id","worker_2_assignment_sha256",
        "worker_2_issue_number","worker_2_author_id",
    }
    if set(barrier_kwargs)!=required:
        return output("BLOCKED","BARRIER_KWARGS_CONTRACT_INVALID")
    base=inspect_two_worker_review_only(**barrier_kwargs)
    if base.status!="BOTH_ARTIFACTS_FOR_REVIEW":
        return output("AWAITING_SCOPED_BASE_RESULTS",
                      "BOTH_GITHUB_COMMENT_ROWS_REQUIRED")
    dbpath=barrier_kwargs["database"]
    root=barrier_kwargs["allowed_experiments_root"]
    if not _safe_db(dbpath,root):
        return output("BLOCKED","ISOLATED_VERIFIED_ARTIFACT_DB_INVALID")
    project=barrier_kwargs["expected_project_id"]
    digest=hashlib.sha256(project.encode("ascii")).hexdigest()
    expected={}
    for idx,commit,path in (
        (1,first_commit_sha,first_artifact_path),
        (2,second_commit_sha,second_artifact_path),
    ):
        assignment=barrier_kwargs["worker_"+str(idx)+"_assignment_id"]
        h=hashlib.sha256((project+":"+assignment).encode("ascii")).hexdigest()
        expected[h]=(
            commit,hashlib.sha256(path.encode("ascii")).hexdigest(),
        )
    try:
        with contextlib.closing(sqlite3.connect(
            dbpath.resolve().as_uri()+"?mode=ro",uri=True,timeout=3,
        )) as conn:
            conn.execute("PRAGMA query_only=ON")
            if conn.execute("PRAGMA quick_check").fetchone()!=("ok",):
                return output("BLOCKED","BLOB_PROOF_LEDGER_INTEGRITY_FAILED")
            total=conn.execute(
                "SELECT COUNT(*) FROM r2_github_review_artifacts "
                "WHERE project_digest=?",
                (digest,),
            ).fetchone()[0]
            if total!=2:
                return output("BLOCKED","PROJECT_ASSIGNMENT_CARDINALITY_INVALID")
            rows=conn.execute(
                "SELECT r.assignment_digest,r.artifact_sha256,"
                "e.artifact_commit_sha,e.artifact_path_digest,"
                "e.artifact_sha256,e.status "
                "FROM r2_github_review_artifacts r JOIN "
                "r2_immutable_artifact_attestations e "
                "ON e.comment_id=r.comment_id "
                "WHERE r.project_digest=?",
                (digest,),
            ).fetchall()
    except (sqlite3.Error,OSError,ValueError,TypeError):
        return output("AWAITING_GIT_BLOB_EVIDENCE",
                      "IMMUTABLE_BLOB_RECEIPT_MISSING_OR_UNREADABLE")
    for row in rows:
        assignment,claimed,commit,path_digest,proven,state=row
        if (assignment not in expected
            or (commit,path_digest)!=expected[assignment]
            or claimed!=proven
            or state!="GIT_BLOB_VERIFIED_FOR_HUMAN_REVIEW"):
            return output("BLOCKED","BLOB_AND_COMMENT_SCOPE_MISMATCH")
    if len(rows)!=2:
        return output("AWAITING_GIT_BLOB_EVIDENCE",
                      "TWO_DISTINCT_BLOB_RECEIPTS_REQUIRED",len(rows))
    return output(
        "BOTH_IMMUTABLE_BLOBS_FOR_HUMAN_REVIEW",
        "TWO_PINNED_ARTIFACTS_NOT_GPT_TERMINAL_EVENTS",
        2,True,
    )
