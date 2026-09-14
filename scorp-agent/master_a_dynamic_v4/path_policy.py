from __future__ import annotations

import os
import pathlib
from collections.abc import Iterable


class PathBoundaryError(ValueError):
    pass


def _key(path: pathlib.Path) -> str:
    return os.path.normcase(str(path))


def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        return os.path.commonpath([_key(path), _key(root)]) == _key(root)
    except ValueError:
        return False


class PathPolicy:
    def __init__(
        self,
        allowed_roots: Iterable[str | pathlib.Path],
        *,
        protected_roots: Iterable[str | pathlib.Path] = (),
    ):
        self.allowed_roots = tuple(pathlib.Path(value).resolve() for value in allowed_roots)
        self.protected_roots = tuple(pathlib.Path(value).resolve() for value in protected_roots)
        if not self.allowed_roots:
            raise PathBoundaryError("ALLOWED_ROOTS_EMPTY")
        if any(not root.is_absolute() for root in self.allowed_roots):
            raise PathBoundaryError("ALLOWED_ROOT_NOT_ABSOLUTE")

    def authorize(
        self, paths: Iterable[str | pathlib.Path], access_mode: str
    ) -> tuple[str, ...]:
        mode = str(access_mode or "").strip().lower()
        if mode not in {"read", "write"}:
            raise PathBoundaryError("ACCESS_MODE_INVALID")
        result: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            candidate = pathlib.Path(raw)
            if not candidate.is_absolute():
                raise PathBoundaryError("PATH_NOT_ABSOLUTE")
            resolved = candidate.resolve(strict=False)
            if not any(_inside(resolved, root) for root in self.allowed_roots):
                raise PathBoundaryError(f"PATH_OUTSIDE_ALLOWLIST: {resolved}")
            if any(_inside(resolved, root) for root in self.protected_roots):
                raise PathBoundaryError(f"PROTECTED_PATH: {resolved}")
            key = _key(resolved)
            if key not in seen:
                result.append(str(resolved))
                seen.add(key)
        if not result:
            raise PathBoundaryError("RESOURCE_SCOPE_EMPTY")
        return tuple(result)
