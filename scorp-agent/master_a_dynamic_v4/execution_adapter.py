"""Fail-closed local execution seam for bounded V4 Worker tasks.

This adapter is intentionally narrower than the legacy GitHub executor.  It can
run only an explicitly allowlisted Python module, never invokes a shell, checks
the assignment identity and path scope before starting a process, and returns
bounded output plus hashes for evidence.  A caller must still obtain the live
lease from ``Scheduler`` before invoking it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import os
import pathlib
import subprocess
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from .path_policy import PathBoundaryError, PathPolicy


UTC = dt.timezone.utc
DEFAULT_ALLOWED_MODULES = frozenset({"master_a_dynamic_v4.csv_workload.cli"})


class ExecutionAdapterRejected(ValueError):
    """Raised before or during an execution request that cannot be trusted."""


@dataclasses.dataclass(frozen=True)
class ExecutionReceipt:
    assignment_id: str
    task_id: str
    command: tuple[str, ...]
    working_directory: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str
    artifact_sha256: dict[str, str]
    started_at: str
    finished_at: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _stamp(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(value: str, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[-limit:]


class LocalExecutionAdapter:
    """Run a small, explicit command surface inside an assignment scope."""

    def __init__(
        self,
        allowed_roots: Iterable[str | pathlib.Path],
        *,
        python_executable: str | pathlib.Path = sys.executable,
        pythonpath: str | pathlib.Path | None = None,
        allowed_modules: Iterable[str] = DEFAULT_ALLOWED_MODULES,
        output_limit: int = 8192,
    ):
        self.path_policy = PathPolicy(allowed_roots)
        self.python_executable = pathlib.Path(python_executable).resolve(strict=True)
        self.pythonpath = pathlib.Path(pythonpath).resolve(strict=True) if pythonpath else None
        self.allowed_modules = frozenset(str(item).strip() for item in allowed_modules if str(item).strip())
        if not self.allowed_modules:
            raise ExecutionAdapterRejected("MODULE_ALLOWLIST_EMPTY")
        if int(output_limit) < 128:
            raise ExecutionAdapterRejected("OUTPUT_LIMIT_INVALID")
        self.output_limit = int(output_limit)

    def execute(
        self,
        claim: Any,
        *,
        module: str,
        args: Sequence[str | pathlib.Path],
        working_directory: str | pathlib.Path,
        resource_paths: Iterable[str | pathlib.Path],
        access_mode: str,
        expected_assignment_id: str | None = None,
        expected_master_epoch: int | None = None,
        expected_lease_token: str | None = None,
        timeout_seconds: int = 300,
    ) -> ExecutionReceipt:
        assignment_id = str(getattr(claim, "assignment_id", "") or "").strip()
        task_id = str(getattr(claim, "task_id", "") or "").strip()
        if not assignment_id or not task_id:
            raise ExecutionAdapterRejected("CLAIM_IDENTITY_MISSING")
        if expected_assignment_id is not None and assignment_id != str(expected_assignment_id):
            raise ExecutionAdapterRejected("CLAIM_ASSIGNMENT_MISMATCH")
        if expected_master_epoch is not None and int(getattr(claim, "master_epoch", -1)) != int(expected_master_epoch):
            raise ExecutionAdapterRejected("CLAIM_EPOCH_MISMATCH")
        if expected_lease_token is not None and str(getattr(claim, "lease_token", "")) != str(expected_lease_token):
            raise ExecutionAdapterRejected("CLAIM_LEASE_MISMATCH")
        module_name = str(module or "").strip()
        if module_name not in self.allowed_modules:
            raise ExecutionAdapterRejected("MODULE_NOT_ALLOWLISTED")
        try:
            timeout = int(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ExecutionAdapterRejected("EXECUTION_TIMEOUT_INVALID") from exc
        if timeout < 1 or timeout > 1800:
            raise ExecutionAdapterRejected("EXECUTION_TIMEOUT_INVALID")
        try:
            cwd = pathlib.Path(working_directory).resolve(strict=True)
            scoped = list(resource_paths)
            authorized = self.path_policy.authorize([cwd, *scoped], access_mode)
        except (FileNotFoundError, PathBoundaryError) as exc:
            raise ExecutionAdapterRejected(str(exc)) from exc
        authorized_keys = {os.path.normcase(str(pathlib.Path(value).resolve(strict=False))) for value in authorized}
        normalized_args: list[str] = []
        for raw in args:
            value = str(raw)
            candidate = pathlib.Path(value)
            if candidate.is_absolute():
                resolved = candidate.resolve(strict=False)
                try:
                    self.path_policy.authorize([resolved], access_mode)
                except PathBoundaryError as exc:
                    raise ExecutionAdapterRejected(str(exc)) from exc
                if os.path.normcase(str(resolved)) not in authorized_keys and not any(
                    os.path.commonpath([os.path.normcase(str(resolved)), root]) == root
                    for root in (os.path.normcase(str(pathlib.Path(item).resolve(strict=False))) for item in authorized)
                ):
                    raise ExecutionAdapterRejected("ARGUMENT_PATH_OUTSIDE_SCOPE")
            elif "\\" in value or "/" in value:
                raise ExecutionAdapterRejected("RELATIVE_PATH_ARGUMENT_UNVERIFIED")
            normalized_args.append(value)
        command = (str(self.python_executable), "-B", "-m", module_name, *normalized_args)
        env = os.environ.copy()
        if self.pythonpath is not None:
            env["PYTHONPATH"] = str(self.pythonpath)
        started = dt.datetime.now(UTC)
        try:
            completed = subprocess.run(
                list(command),
                cwd=str(cwd),
                env=env,
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutionAdapterRejected("EXECUTION_TIMEOUT") from exc
        finished = dt.datetime.now(UTC)
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
        artifact_hashes: dict[str, str] = {}
        for raw in scoped:
            path = pathlib.Path(raw).resolve(strict=False)
            if path.is_file():
                artifact_hashes[str(path)] = _sha256_file(path)
        return ExecutionReceipt(
            assignment_id=assignment_id,
            task_id=task_id,
            command=tuple(command),
            working_directory=str(cwd),
            exit_code=int(completed.returncode),
            stdout=_tail(stdout, self.output_limit),
            stderr=_tail(stderr, self.output_limit),
            stdout_sha256=_sha256_text(stdout),
            stderr_sha256=_sha256_text(stderr),
            artifact_sha256=artifact_hashes,
            started_at=_stamp(started),
            finished_at=_stamp(finished),
        )
