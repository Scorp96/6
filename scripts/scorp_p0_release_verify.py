"""Offline, no-write candidate-to-release manifest authority validator.

A successful offline regression run is not a release approval. For a release,
every pinned source blob must match its manifest SHA-1 *and* SHA-256. This
validator does not contact GitHub, invoke bootstrap, edit files or run binaries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from typing import Any

PROTOCOL = "scorp.exec/v4-release-manifest"
REQUIRED_PATHS = frozenset({
    "scorp-agent/executor-v4.1.ps1",
    "scorp-agent/runner-v4.1.ps1",
    "scorp-agent/executor-v4.schema.json",
    "scorp-agent/bootstrap-v4.ps1",
    "scorp-agent/tests/v4-critical-hardening.ps1",
    "scorp-agent/tests/v4-production-hardening.ps1",
    "scorp-agent/tests/v4-selfheal-hardening.ps1",
})
DIGEST_CHARS = frozenset("0123456789abcdef")


class ReleaseManifestRejected(ValueError):
    """The reviewed manifest cannot authorize the checked-out source."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ReleaseManifestRejected("MANIFEST_DUPLICATE_KEY")
        obj[key] = value
    return obj


def _valid_digest(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= DIGEST_CHARS


def _git_blob_sha1(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\x00" + content).hexdigest()


def evaluate_manifest(repo_root: str | pathlib.Path, manifest: str | pathlib.Path) -> dict[str, Any]:
    """Return bounded audit facts; this operation never writes any path."""
    root = pathlib.Path(repo_root).resolve(strict=True)
    if not root.is_dir():
        raise ReleaseManifestRejected("REPOSITORY_ROOT_NOT_DIRECTORY")
    source = pathlib.Path(manifest)
    if not source.is_absolute():
        source = root / source
    try:
        source = source.resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file():
            raise ReleaseManifestRejected("MANIFEST_OUTSIDE_REPOSITORY")
        raw = source.read_text(encoding="utf-8")
        info = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestRejected("MANIFEST_NOT_READABLE") from exc
    if not isinstance(info, dict) or info.get("protocol_version") != PROTOCOL:
        raise ReleaseManifestRejected("MANIFEST_PROTOCOL_INVALID")
    entries = info.get("files")
    if not isinstance(entries, dict) or set(entries) != REQUIRED_PATHS:
        raise ReleaseManifestRejected("MANIFEST_FILE_SET_INVALID")
    results = []
    blocked = 0
    for relative in sorted(REQUIRED_PATHS):
        expected = entries[relative]
        if not isinstance(expected, dict):
            raise ReleaseManifestRejected("MANIFEST_FILE_ENTRY_INVALID")
        signed_path = pathlib.PurePosixPath(relative)
        if signed_path.is_absolute() or ".." in signed_path.parts:
            raise ReleaseManifestRejected("MANIFEST_SOURCE_PATH_INVALID")
        file_path = root.joinpath(*signed_path.parts)
        try:
            resolved = file_path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ReleaseManifestRejected("MANIFEST_SOURCE_PATH_INVALID")
            if any(x.is_symlink() for x in [file_path, *file_path.parents] if x != root and root in x.parents):
                raise ReleaseManifestRejected("MANIFEST_SOURCE_SYMLINK")
            data = resolved.read_bytes()
        except (OSError, UnicodeError) as exc:
            raise ReleaseManifestRejected("MANIFEST_SOURCE_NOT_READABLE") from exc
        expected_git = expected.get("git_blob_sha")
        expected_sha = expected.get("sha256")
        actual_git = _git_blob_sha1(data)
        actual_sha = hashlib.sha256(data).hexdigest()
        git_state = ("MATCH" if _valid_digest(expected_git, 40) and actual_git == expected_git
                     else "MISMATCH")
        sha_state = ("MATCH" if _valid_digest(expected_sha, 64) and actual_sha == expected_sha
                     else "UNPINNED" if expected_sha == "computed-at-bootstrap"
                     else "MISMATCH")
        if git_state != "MATCH" or sha_state != "MATCH":
            blocked += 1
        results.append({"path": relative, "git_blob": git_state, "sha256": sha_state})
    return {
        "protocol": "scorp.p0-release-preflight/1",
        "result": "READY_FOR_MANUAL_RELEASE_REVIEW" if blocked == 0 else "BLOCKED",
        "total_files": len(results),
        "blocked_files": blocked,
        "checks": results,
        "release_authorized": False,
        "windows_host_authority_attested": False,
        "real_worker_sessions_attested": False,
        "production_modified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--manifest", default="scorp-agent/release-manifest-v4.json")
    args = parser.parse_args(argv)
    try:
        result = evaluate_manifest(args.repo_root, args.manifest)
    except (ReleaseManifestRejected, FileNotFoundError, NotADirectoryError) as exc:
        print(json.dumps({"protocol": "scorp.p0-release-preflight/1",
                          "result": "BLOCKED", "reason": str(exc),
                          "release_authorized": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    # Even clean hashes do not grant deploy permission; exit 0 only allows human review.
    return 0 if result["result"] == "READY_FOR_MANUAL_RELEASE_REVIEW" else 2


if __name__ == "__main__":
    sys.exit(main())
