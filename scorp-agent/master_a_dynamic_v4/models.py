from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any


class CommitResult(str, Enum):
    COMMITTED = "COMMITTED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    FENCED = "FENCED"
    REJECTED = "REJECTED"


class IntentState(str, Enum):
    PREPARED = "PREPARED"
    MAY_HAVE_SUBMITTED = "MAY_HAVE_SUBMITTED"
    CONFIRMED_SUBMITTED = "CONFIRMED_SUBMITTED"
    RESPONSE_CAPTURED = "RESPONSE_CAPTURED"
    VERIFIED_NOT_SUBMITTED = "VERIFIED_NOT_SUBMITTED"
    BLOCKED_AMBIGUOUS = "BLOCKED_AMBIGUOUS"


class AcceptanceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
