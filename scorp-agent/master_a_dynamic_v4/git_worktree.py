"""Fail-closed Git worktree preparation for write-capable Worker assignments.

The manager deliberately exposes a very small Git surface. It verifies a clean
repository and an exact base commit, creates a detached worktree in the
assignment scope, and re-verifies the resulting HEAD. It never removes an
existing directory or runs a shell command; callers remain responsible for
subsequent code execution and final integration review.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from .path_policy import PathBoundaryError, PathPolicy


UTC = dt.timezone.utc


class GitWorktreeRejected(ValueError):
    """Raised before or during worktree creation when a boundary is unsafe."""


@dataclasses.dataclass(frozen=True)
class GitWorktreeReceipt:
    assignment_id: str
    task_id: str
    repository: str
    worktree: str
    base_commit: str
    head_commit: str
    commands: tuple[tuple[str, ...], ...]
    stdout_sha256: str
    stderr_sha256: str
    started_at: str
    finished_at: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _stamp(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        return os.path.commonpath(
            [os.path.normcase(str(path)), os.path.normcase(str(root))]
        ) == os.path.normcase(str(root))
    except ValueError:
        return False


def _inside_scope(path: pathlib.Path, scopes: Iterable[str | pathlib.Path]) -> bool:
    candidate = path.resolve(strict=False)
    for raw_scope in scopes:
        scope = pathlib.Path(raw_scope).resolve(strict=False)
        if scope.exists() and scope.is_dir():
            if _inside(candidate, scope):
                return True
        elif os.path.normcase(str(candidate)) == os.path.normcase(str(scope)):
            return True
    return False


class GitWorktreeManager:
    """Create one detached Git worktree for one write assignment."""

    def __init__(
        self,
        allowed_roots: Iterable[str | pathlib.Path],
        *,
        git_executable: str | pathlib.Path = "git",
        output_limit: int = 8192,
    ):
        self.path_policy = PathPolicy(allowed_roots)
        executable = str(git_executable)
        if not pathlib.Path(executable).is_absolute():
            executable = shutil.which(executable) or ""
        try:
            self.git_executable = pathlib.Path(executable).resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise GitWorktreeRejected("GIT_EXECUTABLE_MISSING") from exc
        if int(output_limit) < 128:
            raise GitWorktreeRejected("OUTPUT_LIMIT_INVALID")
        self.output_limit = int(output_limit)

    def _run(
        self,
        args: tuple[str, ...],
        *,
        cwd: pathlib.Path,
        timeout_seconds: int,
        stdout_parts: list[str],
        stderr_parts: list[str],
    ) -> str:
        command = [str(self.git_executable), *args]
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitWorktreeRejected("GIT_COMMAND_TIMEOUT") from exc
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        stdout_parts.append(stdout)
        stderr_parts.append(stderr)
        if completed.returncode != 0:
            raise GitWorktreeRejected(
                f"GIT_COMMAND_FAILED:{' '.join(args)}:exit={completed.returncode}"
            )
        return stdout.strip()

    def prepare(
        self,
        claim: Any,
        *,
        repository: str | pathlib.Path,
        worktree: str | pathlib.Path,
        base_commit: str,
        timeout_seconds: int = 120,
    ) -> GitWorktreeReceipt:
        assignment_id = str(getattr(claim, "assignment_id", "") or "").strip()
        task_id = str(getattr(claim, "task_id", "") or "").strip()
        if not assignment_id or not task_id:
            raise GitWorktreeRejected("CLAIM_IDENTITY_MISSING")
        if str(getattr(claim, "access_mode", "") or "").strip().lower() != "write":
            raise GitWorktreeRejected("GIT_WORKTREE_REQUIRES_WRITE_ASSIGNMENT")
        scopes = tuple(getattr(claim, "resource_scope", ()) or ())
        if not scopes:
            raise GitWorktreeRejected("CLAIM_SCOPE_MISSING")
        commit = str(base_commit or "").strip().lower()
        if len(commit) not in {40, 64} or any(char not in "0123456789abcdef" for char in commit):
            raise GitWorktreeRejected("BASE_COMMIT_INVALID")
        try:
            timeout = int(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise GitWorktreeRejected("GIT_TIMEOUT_INVALID") from exc
        if timeout < 1 or timeout > 1800:
            raise GitWorktreeRejected("GIT_TIMEOUT_INVALID")

        try:
            repo = pathlib.Path(repository).resolve(strict=True)
            target = pathlib.Path(worktree).resolve(strict=False)
            if not repo.is_dir() or not (repo / ".git").exists():
                raise GitWorktreeRejected("REPOSITORY_NOT_GIT")
            self.path_policy.authorize([repo, target], "write")
        except (FileNotFoundError, PathBoundaryError) as exc:
            raise GitWorktreeRejected(str(exc)) from exc
        if not _inside_scope(target, scopes):
            raise GitWorktreeRejected("WORKTREE_OUTSIDE_ASSIGNMENT_SCOPE")
        if target.exists():
            if target.is_dir() and not any(target.iterdir()):
                raise GitWorktreeRejected("WORKTREE_TARGET_EXISTS")
            raise GitWorktreeRejected("WORKTREE_TARGET_NOT_EMPTY")
        target.parent.mkdir(parents=True, exist_ok=True)

        started = dt.datetime.now(UTC)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        commands: list[tuple[str, ...]] = []

        status_args = ("-C", str(repo), "status", "--porcelain", "--untracked-files=all")
        commands.append(status_args)
        status = self._run(
            status_args,
            cwd=repo,
            timeout_seconds=timeout,
            stdout_parts=stdout_parts,
            stderr_parts=stderr_parts,
        )
        if status:
            raise GitWorktreeRejected("REPOSITORY_DIRTY")

        verify_args = ("-C", str(repo), "rev-parse", "--verify", f"{commit}^{{commit}}")
        commands.append(verify_args)
        observed_base = self._run(
            verify_args,
            cwd=repo,
            timeout_seconds=timeout,
            stdout_parts=stdout_parts,
            stderr_parts=stderr_parts,
        ).lower()
        if observed_base != commit and not commit.startswith(observed_base):
            raise GitWorktreeRejected("BASE_COMMIT_MISMATCH")
        commit = observed_base

        add_args = ("-C", str(repo), "worktree", "add", "--detach", str(target), commit)
        commands.append(add_args)
        self._run(
            add_args,
            cwd=repo,
            timeout_seconds=timeout,
            stdout_parts=stdout_parts,
            stderr_parts=stderr_parts,
        )

        head_args = ("-C", str(target), "rev-parse", "HEAD")
        commands.append(head_args)
        head = self._run(
            head_args,
            cwd=target,
            timeout_seconds=timeout,
            stdout_parts=stdout_parts,
            stderr_parts=stderr_parts,
        ).lower()
        if head != commit:
            raise GitWorktreeRejected("WORKTREE_BASE_MISMATCH")
        clean_args = ("-C", str(target), "status", "--porcelain", "--untracked-files=all")
        commands.append(clean_args)
        if self._run(
            clean_args,
            cwd=target,
            timeout_seconds=timeout,
            stdout_parts=stdout_parts,
            stderr_parts=stderr_parts,
        ):
            raise GitWorktreeRejected("WORKTREE_NOT_CLEAN")
        finished = dt.datetime.now(UTC)
        return GitWorktreeReceipt(
            assignment_id=assignment_id,
            task_id=task_id,
            repository=str(repo),
            worktree=str(target),
            base_commit=commit,
            head_commit=head,
            commands=tuple(commands),
            stdout_sha256=_sha256_text("".join(stdout_parts)),
            stderr_sha256=_sha256_text("".join(stderr_parts)),
            started_at=_stamp(started),
            finished_at=_stamp(finished),
        )

