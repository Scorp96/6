from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .models import canonical_json, sha256_json
from .state_store import StateStore, utc_now


class InstallIdentityError(RuntimeError):
    pass


def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(str(path)), os.path.normcase(str(root))]) == os.path.normcase(str(root))
    except ValueError:
        return False


def _git_head(source_root: pathlib.Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise InstallIdentityError("SOURCE_GIT_HEAD_UNAVAILABLE")
    return completed.stdout.strip().lower()


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_object_metadata(root: pathlib.Path, commit: str, relative: str) -> dict[str, Any] | None:
    """Return immutable Git blob identity when the source is a real checkout."""
    spec = f"{commit}:{relative}"
    oid = subprocess.run(
        ["git", "rev-parse", spec], cwd=root, check=False,
        capture_output=True, text=True, encoding="utf-8",
    )
    if oid.returncode != 0:
        return None
    blob_oid = oid.stdout.strip().lower()
    if len(blob_oid) != 40:
        return None
    content = subprocess.run(
        ["git", "cat-file", "blob", blob_oid], cwd=root, check=False,
        capture_output=True,
    )
    if content.returncode != 0:
        raise InstallIdentityError("SOURCE_GIT_BLOB_UNAVAILABLE")
    return {
        "git_blob_oid": blob_oid,
        "git_blob_sha256": hashlib.sha256(content.stdout).hexdigest(),
        "git_blob_size": len(content.stdout),
    }


def _git_blob_content(root: pathlib.Path, commit: str, relative: str) -> bytes:
    metadata = _git_object_metadata(root, commit, relative)
    if metadata is None:
        raise InstallIdentityError(f"SOURCE_GIT_OBJECT_MISSING:{relative}")
    content = subprocess.run(
        ["git", "cat-file", "blob", metadata["git_blob_oid"]],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if content.returncode != 0:
        raise InstallIdentityError(f"SOURCE_GIT_BLOB_UNAVAILABLE:{relative}")
    return content.stdout


def _validated_relative(root: pathlib.Path, raw: str | pathlib.Path) -> tuple[str, pathlib.Path]:
    relative = pathlib.Path(raw)
    if relative.is_absolute() or not relative.parts:
        raise InstallIdentityError("SOURCE_PATH_ESCAPE")
    try:
        resolved = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise InstallIdentityError("SOURCE_PATH_ESCAPE") from exc
    if not _inside(resolved, root) or not resolved.is_file():
        raise InstallIdentityError("SOURCE_PATH_ESCAPE")
    normalized = relative.as_posix()
    if normalized.startswith("../") or normalized == "..":
        raise InstallIdentityError("SOURCE_PATH_ESCAPE")
    return normalized, resolved


V4_RUNTIME_SOURCE_DIRS = (
    "scorp-agent/master_a_dynamic_v4",
    "scorp-agent/chatgpt-gui-bridge",
)
V4_RUNTIME_EXCLUDED_PARTS = frozenset({"tests", "__pycache__", ".pytest_cache"})


def v4_runtime_release_paths(source_root: str | pathlib.Path) -> tuple[str, ...]:
    """Return the complete source package boundary for one V4 runtime bundle.

    Runtime packaging is intentionally package-based rather than inferred from
    a small import seed. Dynamic imports and public local command surfaces must
    remain installable even when the current daemon entrypoint does not import
    them on a particular path. Tests and generated caches are excluded.
    """
    root = pathlib.Path(source_root).resolve(strict=True)
    files: set[str] = set()
    for relative_root in V4_RUNTIME_SOURCE_DIRS:
        base = (root / pathlib.PurePosixPath(relative_root)).resolve(strict=True)
        if not _inside(base, root) or not base.is_dir():
            raise InstallIdentityError(f"V4_RUNTIME_SOURCE_DIR_MISSING:{relative_root}")
        for item in base.rglob("*"):
            if not item.is_file() or item.is_symlink():
                continue
            rel = item.relative_to(root)
            if any(part in V4_RUNTIME_EXCLUDED_PARTS for part in rel.parts):
                continue
            if item.suffix.lower() in {".pyc", ".pyo"}:
                continue
            files.add(rel.as_posix())
    required = {
        "scorp-agent/master_a_dynamic_v4/__init__.py",
        "scorp-agent/master_a_dynamic_v4/runtime_protocol.py",
        "scorp-agent/master_a_dynamic_v4/runtime_commands.py",
        "scorp-agent/master_a_dynamic_v4/operator_control.py",
        "scorp-agent/master_a_dynamic_v4/runtime_pipe.py",
        "scorp-agent/master_a_dynamic_v4/production_bootstrap.py",
        "scorp-agent/master_a_dynamic_v4/schema.sql",
        "scorp-agent/chatgpt-gui-bridge/tools/v4_daemon_runtime.py",
        "scorp-agent/chatgpt-gui-bridge/tools/v4_release_runtime.py",
        "scorp-agent/chatgpt-gui-bridge/tools/v4_master_controller_runtime.py",
        "scorp-agent/chatgpt-gui-bridge/v4_browser_engine.py",
        "scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py",
        "scorp-agent/chatgpt-gui-bridge/chrome_use_actor_driver_v3.py",
        "scorp-agent/chatgpt-gui-bridge/install-v4-daemon.ps1",
        "scorp-agent/chatgpt-gui-bridge/watch-v4-daemon.ps1",
    }
    missing = sorted(required - files)
    if missing:
        raise InstallIdentityError("V4_RUNTIME_REQUIRED_FILE_MISSING:" + ",".join(missing))
    if not files:
        raise InstallIdentityError("V4_RUNTIME_FILESET_EMPTY")
    return tuple(sorted(files))


def build_v4_runtime_manifest(
    source_root: str | pathlib.Path,
    candidate_commit: str,
    *,
    observed_commit: str | None = None,
) -> dict[str, Any]:
    return build_candidate_manifest(
        source_root,
        candidate_commit,
        v4_runtime_release_paths(source_root),
        observed_commit=observed_commit,
    )


def build_candidate_manifest(
    source_root: str | pathlib.Path,
    candidate_commit: str,
    relative_paths: Sequence[str | pathlib.Path],
    *,
    observed_commit: str | None = None,
) -> dict[str, Any]:
    root = pathlib.Path(source_root).resolve(strict=True)
    candidate = str(candidate_commit or "").strip().lower()
    actual = str(observed_commit or _git_head(root)).strip().lower()
    if len(candidate) != 40 or candidate != actual:
        raise InstallIdentityError("CANDIDATE_COMMIT_MISMATCH")
    files: dict[str, dict[str, Any]] = {}
    for raw in relative_paths:
        relative, source = _validated_relative(root, raw)
        if relative in files:
            raise InstallIdentityError("SOURCE_PATH_DUPLICATE")
        stat = source.stat()
        metadata: dict[str, Any] = {"sha256": _file_sha256(source), "size": stat.st_size}
        git_metadata = _git_object_metadata(root, candidate, relative)
        if git_metadata is not None:
            # The release identity is the immutable Git blob.  A Windows
            # checkout may materialize the same text with different EOL bytes;
            # those working-tree bytes must not redefine the candidate.
            metadata["sha256"] = git_metadata["git_blob_sha256"]
            metadata["size"] = git_metadata["git_blob_size"]
            metadata.update(git_metadata)
        files[relative] = metadata
    if not files:
        raise InstallIdentityError("CANDIDATE_FILESET_EMPTY")
    core = {
        "format": "scorp-v4-candidate-manifest/1",
        "candidate_commit": candidate,
        "source_tree": str(root),
        "files": {key: files[key] for key in sorted(files)},
    }
    return {**core, "manifest_sha256": sha256_json(core)}


def verify_candidate_manifest(
    source_root: str | pathlib.Path,
    manifest: Mapping[str, Any],
    *,
    expected_commit: str,
    observed_commit: str | None = None,
) -> None:
    root = pathlib.Path(source_root).resolve(strict=True)
    candidate = str(expected_commit or "").strip().lower()
    actual = str(observed_commit or _git_head(root)).strip().lower()
    if str(manifest.get("candidate_commit")) != candidate:
        raise InstallIdentityError("CANDIDATE_COMMIT_MISMATCH")
    if str(manifest.get("source_tree")) != str(root):
        raise InstallIdentityError("SOURCE_TREE_MISMATCH")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise InstallIdentityError("CANDIDATE_FILESET_EMPTY")
    # A later documentation commit may be checked out around an immutable
    # candidate. Git blob metadata is sufficient to verify the candidate in
    # that case; manifests without blob identity still require HEAD equality.
    if candidate != actual and not all(isinstance(value, Mapping) and value.get("git_blob_oid") for value in files.values()):
        raise InstallIdentityError("CANDIDATE_COMMIT_MISMATCH")
    core = {key: manifest[key] for key in ("format", "candidate_commit", "source_tree", "files")}
    if str(manifest.get("manifest_sha256")) != sha256_json(core):
        raise InstallIdentityError("CANDIDATE_MANIFEST_HASH_MISMATCH")
    for raw, metadata in files.items():
        _, source = _validated_relative(root, str(raw))
        if not isinstance(metadata, Mapping):
            raise InstallIdentityError("SOURCE_FILE_METADATA_INVALID")
        git_oid = str(metadata.get("git_blob_oid") or "").strip().lower()
        if git_oid:
            git_metadata = _git_object_metadata(root, candidate, str(raw))
            if git_metadata is None or git_metadata["git_blob_oid"] != git_oid:
                raise InstallIdentityError(f"SOURCE_GIT_OBJECT_MISMATCH:{raw}")
            if str(metadata.get("sha256")) != git_metadata["git_blob_sha256"] or int(metadata.get("size", -1)) != git_metadata["git_blob_size"]:
                raise InstallIdentityError(f"SOURCE_GIT_METADATA_MISMATCH:{raw}")
        else:
            if _file_sha256(source) != str(metadata.get("sha256")) or source.stat().st_size != int(metadata.get("size", -1)):
                raise InstallIdentityError(f"SOURCE_FILE_HASH_MISMATCH:{raw}")


def install_runtime_release(
    source_root: str | pathlib.Path,
    release_root: str | pathlib.Path,
    manifest: Mapping[str, Any],
    *,
    protected_roots: Iterable[str | pathlib.Path] = (),
    observed_commit: str | None = None,
) -> dict[str, Any]:
    """Install one immutable runtime code release without creating project state."""

    source = pathlib.Path(source_root).resolve(strict=True)
    target = pathlib.Path(release_root).resolve(strict=False)
    protected = tuple(pathlib.Path(value).resolve(strict=False) for value in protected_roots)
    if any(_inside(target, root) or _inside(root, target) for root in protected):
        raise InstallIdentityError("RELEASE_TARGET_PROTECTED")
    if _inside(source, target) or _inside(target, source):
        raise InstallIdentityError("RELEASE_SOURCE_OVERLAP")
    verify_candidate_manifest(
        source,
        manifest,
        expected_commit=str(manifest.get("candidate_commit") or ""),
        observed_commit=observed_commit,
    )
    if target.exists() and any(target.iterdir()):
        raise InstallIdentityError("RELEASE_TARGET_NOT_EMPTY")
    before = snapshot_paths(protected)
    target.mkdir(parents=True, exist_ok=True)
    for relative in sorted(manifest["files"]):
        _, source_file = _validated_relative(source, relative)
        destination = (target / pathlib.PurePosixPath(relative)).resolve(strict=False)
        if not _inside(destination, target):
            raise InstallIdentityError("INSTALL_DESTINATION_ESCAPE")
        destination.parent.mkdir(parents=True, exist_ok=True)
        metadata = manifest["files"][relative]
        if metadata.get("git_blob_oid"):
            destination.write_bytes(
                _git_blob_content(source, str(manifest["candidate_commit"]), relative)
            )
        else:
            shutil.copy2(source_file, destination)
        if _file_sha256(destination) != str(metadata["sha256"]):
            raise InstallIdentityError(f"INSTALLED_FILE_HASH_MISMATCH:{relative}")
    manifest_path = target / "candidate-manifest.json"
    manifest_path.write_text(canonical_json(dict(manifest)) + "\n", encoding="utf-8", newline="\n")
    after = snapshot_paths(protected)
    if before != after:
        raise InstallIdentityError("PROTECTED_PATH_MUTATION")
    receipt = {
        "format": "scorp-v4-runtime-release-install/1",
        "candidate_commit": str(manifest["candidate_commit"]),
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "source_tree": str(source),
        "release_root": str(target),
        "file_count": len(manifest["files"]),
        "protected_before_sha256": sha256_json(before),
        "protected_after_sha256": sha256_json(after),
        "installed_at": utc_now(),
    }
    (target / "release-receipt.json").write_text(
        canonical_json(receipt) + "\n", encoding="utf-8", newline="\n"
    )
    verify_runtime_release(target, receipt, manifest)
    return receipt


def verify_runtime_release(
    release_root: str | pathlib.Path,
    receipt: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    """Verify the installed bytes used by a persistent runtime before startup."""

    target = pathlib.Path(release_root).resolve(strict=True)
    if str(receipt.get("format") or "") != "scorp-v4-runtime-release-install/1":
        raise InstallIdentityError("RELEASE_RECEIPT_FORMAT_INVALID")
    if str(receipt.get("release_root") or "") != str(target):
        raise InstallIdentityError("RELEASE_ROOT_MISMATCH")
    if str(receipt.get("candidate_commit") or "") != str(manifest.get("candidate_commit") or ""):
        raise InstallIdentityError("RELEASE_CANDIDATE_MISMATCH")
    if str(receipt.get("manifest_sha256") or "") != str(manifest.get("manifest_sha256") or ""):
        raise InstallIdentityError("RELEASE_MANIFEST_MISMATCH")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise InstallIdentityError("CANDIDATE_FILESET_EMPTY")
    if int(receipt.get("file_count", -1)) != len(files):
        raise InstallIdentityError("RELEASE_FILE_COUNT_MISMATCH")
    for relative, metadata in files.items():
        destination = (target / pathlib.PurePosixPath(str(relative))).resolve(strict=True)
        if not _inside(destination, target):
            raise InstallIdentityError("INSTALL_DESTINATION_ESCAPE")
        if not isinstance(metadata, Mapping) or _file_sha256(destination) != str(metadata.get("sha256") or ""):
            raise InstallIdentityError(f"INSTALLED_FILE_HASH_MISMATCH:{relative}")
    stored_manifest = json.loads((target / "candidate-manifest.json").read_text(encoding="utf-8"))
    if dict(stored_manifest) != dict(manifest):
        raise InstallIdentityError("INSTALLED_MANIFEST_MISMATCH")
    stored_receipt = json.loads((target / "release-receipt.json").read_text(encoding="utf-8"))
    if dict(stored_receipt) != dict(receipt):
        raise InstallIdentityError("RELEASE_RECEIPT_MISMATCH")


def snapshot_paths(paths: Iterable[str | pathlib.Path]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for raw_root in paths:
        root = pathlib.Path(raw_root).resolve(strict=False)
        key = str(root)
        if not root.exists():
            snapshot[key] = {"exists": False, "files": {}}
            continue
        files: dict[str, dict[str, Any]] = {}
        if root.is_file():
            files["."] = {"sha256": _file_sha256(root), "size": root.stat().st_size}
        else:
            for directory, names, filenames in os.walk(root, followlinks=False):
                names.sort()
                filenames.sort()
                base = pathlib.Path(directory)
                for name in filenames:
                    item = base / name
                    relative = item.relative_to(root).as_posix()
                    if item.is_symlink():
                        files[relative] = {"symlink": os.readlink(item)}
                    else:
                        files[relative] = {"sha256": _file_sha256(item), "size": item.stat().st_size}
        snapshot[key] = {"exists": True, "files": files}
    return {key: snapshot[key] for key in sorted(snapshot)}


def install_to_lab(
    source_root: str | pathlib.Path,
    lab_root: str | pathlib.Path,
    manifest: Mapping[str, Any],
    *,
    protected_roots: Iterable[str | pathlib.Path],
    interpreter: str | pathlib.Path,
    observed_commit: str | None = None,
) -> dict[str, Any]:
    source = pathlib.Path(source_root).resolve(strict=True)
    target = pathlib.Path(lab_root).resolve(strict=False)
    protected = tuple(pathlib.Path(value).resolve(strict=False) for value in protected_roots)
    interpreter_text = str(interpreter or "").strip()
    interpreter_candidate = pathlib.Path(interpreter_text)
    if interpreter_candidate.is_absolute():
        interpreter_path = interpreter_candidate.resolve(strict=True)
    else:
        discovered = shutil.which(interpreter_text)
        if not discovered:
            raise InstallIdentityError("INTERPRETER_NOT_FOUND")
        interpreter_path = pathlib.Path(discovered).resolve(strict=True)
    if any(_inside(target, root) or _inside(root, target) for root in protected):
        raise InstallIdentityError("LAB_TARGET_PROTECTED")
    if _inside(source, target) or _inside(target, source):
        raise InstallIdentityError("LAB_SOURCE_OVERLAP")
    verify_candidate_manifest(
        source,
        manifest,
        expected_commit=str(manifest.get("candidate_commit") or ""),
        observed_commit=observed_commit,
    )
    if target.exists() and any(target.iterdir()):
        raise InstallIdentityError("LAB_TARGET_NOT_EMPTY")
    before = snapshot_paths(protected)
    target.mkdir(parents=True, exist_ok=True)
    for relative in sorted(manifest["files"]):
        _, source_file = _validated_relative(source, relative)
        destination = (target / pathlib.PurePosixPath(relative)).resolve(strict=False)
        if not _inside(destination, target):
            raise InstallIdentityError("INSTALL_DESTINATION_ESCAPE")
        destination.parent.mkdir(parents=True, exist_ok=True)
        metadata = manifest["files"][relative]
        if metadata.get("git_blob_oid"):
            destination.write_bytes(_git_blob_content(source, str(manifest["candidate_commit"]), relative))
        else:
            shutil.copy2(source_file, destination)
    database = target / "state.sqlite3"
    StateStore(database, allowed_roots=[target]).close()
    after = snapshot_paths(protected)
    if before != after:
        raise InstallIdentityError("PROTECTED_PATH_MUTATION")
    receipt = {
        "format": "scorp-v4-lab-install-receipt/1",
        "source_commit": str(manifest["candidate_commit"]),
        "source_tree": str(source),
        "lab_root": str(target),
        "interpreter": str(interpreter_path),
        "database": str(database.resolve()),
        "manifest_sha256": str(manifest["manifest_sha256"]),
        "protected_before_sha256": sha256_json(before),
        "protected_after_sha256": sha256_json(after),
        "installed_at": utc_now(),
    }
    receipt_path = target / "install-receipt.json"
    receipt_path.write_text(canonical_json(receipt) + "\n", encoding="utf-8", newline="\n")
    return receipt


def verify_install_receipt(
    lab_root: str | pathlib.Path,
    receipt: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    target = pathlib.Path(lab_root).resolve(strict=True)
    if str(receipt.get("lab_root")) != str(target):
        raise InstallIdentityError("INSTALL_ROOT_MISMATCH")
    if str(receipt.get("manifest_sha256")) != str(manifest.get("manifest_sha256")):
        raise InstallIdentityError("INSTALL_MANIFEST_MISMATCH")
    for relative, metadata in manifest["files"].items():
        destination = (target / pathlib.PurePosixPath(relative)).resolve(strict=True)
        if not _inside(destination, target) or _file_sha256(destination) != str(metadata["sha256"]):
            raise InstallIdentityError(f"INSTALLED_FILE_HASH_MISMATCH:{relative}")
    stored = json.loads((target / "install-receipt.json").read_text(encoding="utf-8"))
    if dict(receipt) != stored:
        raise InstallIdentityError("INSTALL_RECEIPT_MISMATCH")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install an exact SCORP V4 candidate into an isolated lab")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--interpreter", default=sys.executable)
    parser.add_argument("--protected", action="append", default=[])
    args = parser.parse_args(argv)
    manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))
    if str(manifest.get("candidate_commit")) != args.candidate:
        raise InstallIdentityError("CANDIDATE_COMMIT_MISMATCH")
    receipt = install_to_lab(
        args.source,
        args.target,
        manifest,
        protected_roots=args.protected,
        interpreter=args.interpreter,
    )
    sys.stdout.write(canonical_json(receipt) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
