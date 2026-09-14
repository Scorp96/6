import datetime as _dt
import base64
import hashlib
import json
import re

_ALLOWED_MUTATIONS = {"SET_NEXT_ACTION", "SET_TERMINAL", "SET_WAITING", "SET_BLOCKED", "CREATE_CHILD_TASKS"}
_ALLOWED_ROLE_RESPONSES = {"DISPATCH", "WAIT", "TERMINAL", "HANDOFF", "BLOCKER"}


def find_chat_editor(snapshot: str):
    patterns = [
        r'\((\d+),(\d+)\)\s+(?:编辑|edit)\s+"(?:与 ChatGPT 聊天|Message ChatGPT)"',
        r'\((\d+),(\d+)\).*?(?:与 ChatGPT 聊天|Message ChatGPT)',
    ]
    for pattern in patterns:
        m = re.search(pattern, snapshot, re.I)
        if m:
            return int(m.group(1)), int(m.group(2))
    raise ValueError("CHAT_EDITOR_NOT_FOUND")


def response_is_terminal(snapshot: str) -> bool:
    nonterminal = ("停止回答", "Stop generating", "Stop responding")
    return not any(x in snapshot for x in nonterminal)


def _validate_mutation(mutation: dict):
    if not isinstance(mutation, dict):
        raise ValueError("MUTATION_NOT_OBJECT")
    kind = mutation.get("kind")
    if kind not in _ALLOWED_MUTATIONS:
        raise ValueError("MUTATION_KIND_INVALID")
    if kind == "SET_NEXT_ACTION" and not isinstance(mutation.get("next_action"), dict):
        raise ValueError("NEXT_ACTION_REQUIRED")
    if kind == "SET_TERMINAL" and mutation.get("state", "DONE") not in {"DONE", "FAILED"}:
        raise ValueError("TERMINAL_STATE_INVALID")
    return mutation


def _validate_role_response(value: dict):
    if not isinstance(value, dict):
        raise ValueError("ROLE_RESPONSE_NOT_OBJECT")
    if value.get("kind") not in _ALLOWED_ROLE_RESPONSES:
        raise ValueError("ROLE_RESPONSE_KIND_INVALID")
    return value


def extract_role_response(snapshot: str, turn_id: str):
    marker = f"SCORP_GUI_ROLE_V1::{turn_id}::"
    tokens = re.findall(re.escape(marker) + r"([A-Za-z0-9_-]+)", snapshot or "")
    if not tokens:
        raise ValueError("ROLE_RESPONSE_MARKER_COUNT_0")
    unique = {}
    for token in tokens:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            parsed = _validate_role_response(json.loads(raw.decode("utf-8")))
            canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            unique[canonical] = parsed
        except Exception:
            continue
    if not unique:
        raise ValueError("ROLE_RESPONSE_INVALID")
    if len(unique) != 1:
        raise ValueError(f"ROLE_RESPONSE_UNIQUE_COUNT_{len(unique)}")
    return next(iter(unique.values()))



_ALLOWED_MASTER_ACTOR_RESPONSES = {"DISPATCH", "CONTINUE", "DRAIN", "WAIT", "TERMINAL"}
_ALLOWED_WORKER_ACTOR_RESPONSES = {"HANDOFF", "BLOCKER"}


def _validate_actor_response_v3(value: dict, actor_kind: str):
    if not isinstance(value, dict):
        raise ValueError("ACTOR_RESPONSE_NOT_OBJECT")
    kind = str(value.get("kind") or "").upper()
    actor_kind = str(actor_kind or "").upper()
    allowed = _ALLOWED_MASTER_ACTOR_RESPONSES if actor_kind == "MASTER" else _ALLOWED_WORKER_ACTOR_RESPONSES if actor_kind == "WORKER" else None
    if allowed is None:
        raise ValueError("ACTOR_KIND_INVALID")
    if kind not in allowed:
        raise ValueError("ACTOR_RESPONSE_KIND_INVALID")
    normalized = dict(value)
    normalized["kind"] = kind
    return normalized


def extract_actor_response_v3(snapshot: str, turn_id: str, actor_kind: str):
    marker = f"SCORP_GUI_ACTOR_V3::{turn_id}::"
    tokens = re.findall(re.escape(marker) + r"([A-Za-z0-9_-]+)", snapshot or "")
    if not tokens:
        raise ValueError("ACTOR_RESPONSE_MARKER_COUNT_0")
    unique = {}
    for token in tokens:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            parsed = _validate_actor_response_v3(json.loads(raw.decode("utf-8")), actor_kind)
            canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            unique[canonical] = parsed
        except Exception:
            continue
    if not unique:
        raise ValueError("ACTOR_RESPONSE_INVALID")
    if len(unique) != 1:
        raise ValueError(f"ACTOR_RESPONSE_UNIQUE_COUNT_{len(unique)}")
    return next(iter(unique.values()))

def extract_mutation(snapshot: str, request_id: str):
    v2 = f"SCORP_GUI_MUTATION_V2::{request_id}::"
    tokens = re.findall(re.escape(v2) + r"([A-Za-z0-9_-]+)", snapshot)
    if tokens:
        unique = {}
        for token in tokens:
            try:
                raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
                parsed = _validate_mutation(json.loads(raw.decode("utf-8")))
                canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                unique[canonical] = parsed
            except Exception:
                continue
        if not unique:
            raise ValueError("MUTATION_V2_INVALID")
        if len(unique) != 1:
            raise ValueError(f"MUTATION_UNIQUE_COUNT_{len(unique)}")
        return next(iter(unique.values()))
    marker = f"SCORP_GUI_MUTATION_V1::{request_id}::"
    starts = [m.start() for m in re.finditer(re.escape(marker), snapshot)]
    if not starts:
        raise ValueError("MUTATION_MARKER_COUNT_0")
    decoder = json.JSONDecoder()
    unique = {}
    for start in starts:
        tail = snapshot[start + len(marker):]
        parsed = None
        for candidate in (tail, tail.replace(chr(92) + '"', '"')):
            candidate = candidate.lstrip()
            try:
                obj, _ = decoder.raw_decode(candidate)
                parsed = _validate_mutation(obj)
                break
            except (json.JSONDecodeError, ValueError):
                continue
        if parsed is None:
            continue
        canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        unique[canonical] = parsed
    if not unique:
        raise ValueError("MUTATION_JSON_INVALID")
    if len(unique) != 1:
        raise ValueError(f"MUTATION_UNIQUE_COUNT_{len(unique)}")
    return next(iter(unique.values()))


def build_bound_decision(request: dict, mutation: dict):
    if request.get("safety_class") != "standard":
        raise ValueError("AUTO_SAFETY_CLASS_REJECTED")
    mutation = _validate_mutation(mutation)
    binding = "|".join(str(request.get(k, "")) for k in (
        "request_id", "task_id", "registry_sequence", "continuation_generation",
        "previous_action_id", "previous_result_sha256"))
    decision_id = "gui-" + hashlib.sha256(binding.encode("utf-8")).hexdigest()[:32]
    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "protocol_version": "scorp.orchestrator/continuation-decision-v1",
        "decision_id": decision_id,
        "request_id": request["request_id"],
        "task_id": request["task_id"],
        "expected_registry_sequence": int(request["registry_sequence"]),
        "previous_action_id": request.get("previous_action_id"),
        "previous_result_sha256": request.get("previous_result_sha256"),
        "previous_continuation_generation": int(request["continuation_generation"]),
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + _dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "mutation": mutation,
    }
