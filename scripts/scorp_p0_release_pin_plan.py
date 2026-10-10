"""Print a no-write SHA-pinned release review plan from immutable local Git HEAD.

This does NOT update any release manifest or authorize deployment. It is only
a bill of materials for explicit operator review and independently reproduced
file hashes. The actual release remains BLOCKED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

from scorp_p0_release_verify import (
    PROTOCOL, REQUIRED_PATHS, ReleaseManifestRejected,
    _git_blob_sha1, _git_committed_bytes, evaluate_manifest,
)


def _git_head(root: pathlib.Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseManifestRejected("PIN_PLAN_GIT_HEAD_UNAVAILABLE") from exc
    value = proc.stdout.decode("ascii", errors="ignore").strip()
    if proc.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ReleaseManifestRejected("PIN_PLAN_GIT_HEAD_UNAVAILABLE")
    return value


def build_pin_plan(repo_root: pathlib.Path | str, *, expected_head: str | None = None) -> dict:
    root = pathlib.Path(repo_root).resolve(strict=True)
    if not root.is_dir():
        raise ReleaseManifestRejected("PIN_PLAN_ROOT_INVALID")
    head = _git_head(root)
    if expected_head is not None and (not re.fullmatch(r"[0-9a-f]{40}", expected_head)
                                      or expected_head != head):
        raise ReleaseManifestRejected("PIN_PLAN_EXPECTED_COMMIT_MISMATCH")
    files = {}
    for name in sorted(REQUIRED_PATHS):
        blob = _git_committed_bytes(root, name)
        files[name] = {
            "git_blob_sha": _git_blob_sha1(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
    existing = evaluate_manifest(
        root, "scorp-agent/release-manifest-v4.json", source_mode="git",
    )
    manifest_proposal = {
        "protocol_version": PROTOCOL,
        "release": "P0_REVIEW_ONLY_UNAPPROVED",
        "source_commit_sha": head,
        "files": files,
    }
    canonical = json.dumps(
        manifest_proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return {
        "protocol": "scorp.p0-release-pin-review-plan/1",
        "candidate_source_commit_sha": head,
        "proposed_manifest_sha256": hashlib.sha256(canonical).hexdigest(),
        "proposed_manifest": manifest_proposal,
        "current_manifest_result": existing["result"],
        "current_manifest_blocked_files": existing["blocked_files"],
        "release_authorized": False,
        "operator_signoff_attested": False,
        "windows_host_attested": False,
        "production_modified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--expected-head", default=None)
    options = parser.parse_args(argv)
    try:
        report = build_pin_plan(options.repo_root, expected_head=options.expected_head)
    except (ReleaseManifestRejected, OSError, ValueError) as exc:
        print(json.dumps({
            "protocol": "scorp.p0-release-pin-review-plan/1",
            "status": "BLOCKED",
            "reason": str(exc),
            "release_authorized": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
