from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import datetime as dt
import time
from pathlib import Path
from urllib.parse import urlsplit

from gui_transport import validate_conversation_url
from gui_transport import chatgpt_throttle_visible
from v4_auth import classify_chatgpt_snapshot

_ROOT_URL = "https://chatgpt.com/"
_PROTOCOL = "scorp.chrome-use-driver/v1"
_ALLOWED_ACTORS = {"MASTER", "WORKER"}
_LIFECYCLE_ROLES = {"MASTER", "WORKER", "DIAGNOSTIC", "UNKNOWN"}
_SEND_BUTTON_NAMES = {
    "发送提示",
    "发送提示词",
    "发送消息",
    "send",
    "send prompt",
    "send message",
}
_RATE_LIMIT_ACK_BUTTON_NAMES = {
    "确定",
    "好的",
    "明白",
    "明白了",
    "got it",
    "ok",
    "okay",
}
_STATE_LOCKS: dict[str, threading.RLock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


class SendControlResolutionError(ValueError):
    """Safe diagnostics for a post-fill snapshot without page text."""

    def __init__(self, reason: str, payload):
        self.reason = reason
        self.payload = payload
        rendered = _render_payload(payload)
        try:
            refs = _refs_from_snapshot(payload)
        except ValueError:
            refs = {}
        button_names = sorted(
            {
                _control_label(meta)
                for meta in refs.values()
                if isinstance(meta, dict)
                and str(meta.get("role") or "").strip().lower() == "button"
                and _control_label(meta)
            }
        )
        self.diagnostics = {
            "button_names": button_names,
            "button_count": len(button_names),
            "ref_count": len(refs),
            "snapshot_sha256": _sha(rendered),
        }
        self._refresh_message()

    def _refresh_message(self):
        super().__init__(
            f"{self.reason};diagnostics="
            + json.dumps(self.diagnostics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )

    def add_context(self, **values):
        self.diagnostics.update(values)
        self._refresh_message()


def _state_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(key, threading.RLock())


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_url(value, *, allow_root=False):
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme.casefold() == "https" and parsed.netloc.casefold() == "chatgpt.com":
        path = parsed.path.rstrip("/")
        if not path:
            if allow_root:
                return _ROOT_URL
            raise ValueError("ACTOR_GUI_CONVERSATION_URL_MISSING")
        if re.fullmatch(r"/c/[A-Za-z0-9-]+", path):
            # Query and fragment parameters on a ChatGPT conversation are UI
            # routing details; the canonical identity is the /c/<id> path.
            return f"https://chatgpt.com{path}"
    return validate_conversation_url(text)


def _extract_scalar(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("url", "value", "result", "text", "snapshot"):
            if key in value:
                found = _extract_scalar(value[key])
                if found is not None:
                    return found
        if "data" in value:
            return _extract_scalar(value["data"])
    if isinstance(value, list) and len(value) == 1:
        return _extract_scalar(value[0])
    return None


def _extract_render_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("snapshot", "content", "text", "result", "value"):
            if key in value:
                found = _extract_render_text(value[key])
                if found is not None:
                    return found
        if "data" in value:
            found = _extract_render_text(value["data"])
            if found is not None:
                return found
    if isinstance(value, list) and len(value) == 1:
        return _extract_render_text(value[0])
    return None


def _render_payload(value) -> str:
    if isinstance(value, str):
        return value
    found = _extract_render_text(value)
    if found is not None:
        return str(found)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _control_label(meta: dict) -> str:
    """Return a stable accessibility label without reading page body text."""

    for key in ("name", "aria-label", "ariaLabel", "label", "title", "description"):
        value = " ".join(str(meta.get(key) or "").split())
        if value:
            return value
    return ""


def _send_control_name(meta: dict) -> str:
    label = _control_label(meta).casefold()
    # Chrome Use may append a keyboard shortcut to the accessible name.
    return re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()


def _refs_from_snapshot(payload):
    if not isinstance(payload, dict):
        raise ValueError("CHROME_USE_EDITOR_SNAPSHOT_INVALID")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("CHROME_USE_EDITOR_SNAPSHOT_INVALID")
    refs = data.get("refs")
    if not isinstance(refs, dict):
        raise ValueError("CHROME_USE_EDITOR_REFS_MISSING")
    return refs


def _editor_ref_from_snapshot(payload) -> str:
    refs = _refs_from_snapshot(payload)
    candidates = []
    for ref, meta in refs.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("role") or "").strip().lower() == "textbox":
            candidates.append(str(ref))
    candidates = sorted(set(candidates))
    if len(candidates) != 1:
        raise ValueError(f"CHROME_USE_EDITOR_REF_COUNT_{len(candidates)}")
    return "@" + candidates[0]


def _send_ref_from_snapshot(payload) -> str:
    refs = _refs_from_snapshot(payload)
    candidates = []
    for ref, meta in refs.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("role") or "").strip().lower() != "button":
            continue
        if _send_control_name(meta) in _SEND_BUTTON_NAMES:
            candidates.append(str(ref))
    candidates = sorted(set(candidates))
    if len(candidates) != 1:
        raise ValueError(f"CHROME_USE_SEND_REF_COUNT_{len(candidates)}")
    return "@" + candidates[0]


def _prompt_observation_from_snapshot(payload, expected_prompt: str) -> str:
    """Classify an explicitly exposed composer value without guessing.

    Chrome Use does not include the textbox value in every accessibility
    snapshot.  Treat that absence as ``UNREPORTED`` so legacy transports keep
    their existing contract; only an explicit textbox value that differs from
    the intended prompt is a deterministic pre-submit failure.
    """

    expected = str(expected_prompt or "")
    if not expected:
        return "UNREPORTED"
    rendered = _render_payload(payload)
    if expected in rendered:
        return "MATCH"
    explicit_value = False
    partial_value = False
    try:
        refs = _refs_from_snapshot(payload)
    except ValueError:
        refs = {}
    for meta in refs.values():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("role") or "").strip().lower() != "textbox":
            continue
        for key in ("value", "text", "content"):
            if key not in meta:
                continue
            explicit_value = True
            actual = str(meta.get(key) or "")
            if expected in actual:
                return "MATCH"
            if actual and len(actual) < len(expected) and expected.startswith(actual):
                partial_value = True
    for line in rendered.splitlines():
        if "textbox" not in line.casefold() or "[ref=" not in line:
            continue
        marker = re.search(r"\]\s*:\s*(.*)$", line)
        if marker is None:
            continue
        explicit_value = True
        actual = marker.group(1)
        if expected in actual:
            return "MATCH"
        if actual and len(actual) < len(expected) and expected.startswith(actual):
            # Accessibility snapshots may put only the first line of a
            # multiline textbox on the ref line and expose the remaining
            # lines as continuation text.  That is incomplete evidence, not
            # proof that the composer contains the wrong value.
            partial_value = True
    if partial_value:
        return "UNREPORTED"
    return "MISMATCH" if explicit_value else "UNREPORTED"


def _composer_ready_or_unreported(payload) -> bool:
    """Accept text-only auth snapshots; gate only an explicit empty refs tree.

    Chrome Use can return page text and accessibility refs in separate
    snapshots.  A text-only authenticated response therefore cannot prove
    that the composer is absent; the caller will obtain a fresh refs snapshot.
    When refs are present, however, recovery must wait for exactly one
    textbox before reporting the page ready.
    """

    try:
        _refs_from_snapshot(payload)
    except ValueError:
        return True
    try:
        _editor_ref_from_snapshot(payload)
    except ValueError:
        return False
    return True


def _rate_limit_ack_ref_from_snapshot(payload) -> str:
    """Resolve exactly one acknowledgement control on a known throttle dialog."""

    refs = _refs_from_snapshot(payload)
    candidates = []
    for ref, meta in refs.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("role") or "").strip().lower() != "button":
            continue
        label = _control_label(meta)
        # Chrome Use may append a shortcut to the accessible label.
        normalized = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip().casefold()
        if normalized in _RATE_LIMIT_ACK_BUTTON_NAMES:
            candidates.append(str(ref))
    candidates = sorted(set(candidates))
    if len(candidates) != 1:
        raise ValueError(f"CHROME_USE_RATE_LIMIT_ACK_REF_COUNT_{len(candidates)}")
    return "@" + candidates[0]


def _chrome_use_daemon_busy_error(exc: Exception) -> bool:
    message = str(exc or "").casefold()
    return (
        "chrome_use_exit_1" in message
        and ("daemon may be busy" in message or "eof while parsing" in message)
    )


class ChromeUseActorDriverV3:
    def __init__(self, cli, state_path, *, sleeper=asyncio.sleep, timeout_seconds=30, clock=time.monotonic):
        self.cli = cli
        self.state_path = Path(state_path)
        self.sleeper = sleeper
        self.clock = clock
        self.timeout_seconds = int(timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("CHROME_USE_DRIVER_TIMEOUT_INVALID")
        # Multiple Worker browser submissions share this state file.  Protect
        # the read/modify/write transitions across driver instances in the
        # same host process; a fixed temp path without a lock loses bindings.
        self._state_mutex = _state_lock(self.state_path)
        # Chrome Use exposes one relay/foreground target even when logical
        # sessions have distinct tabs. Serialize each complete submit
        # transaction so tab selection, fill, click, and URL promotion cannot
        # be interleaved by another Worker.
        self._submission_mutex = threading.Lock()

    def _load(self):
        with self._state_mutex:
            if not self.state_path.is_file():
                return {"protocol_version": _PROTOCOL, "turns": {}, "conversations": {}, "sessions": {}}
            value = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict) or value.get("protocol_version") != _PROTOCOL:
                raise ValueError("CHROME_USE_DRIVER_STATE_INVALID")
            if not isinstance(value.get("turns"), dict) or not isinstance(value.get("conversations"), dict):
                raise ValueError("CHROME_USE_DRIVER_STATE_INVALID")
            # Older V4 state files predate lifecycle hygiene.  Migrate them
            # in memory without changing their durable bindings until the next
            # normal state transition saves the file.
            if "sessions" not in value:
                value["sessions"] = {}
            if not isinstance(value.get("sessions"), dict):
                raise ValueError("CHROME_USE_DRIVER_STATE_INVALID")
            return value

    def _save(self, value):
        with self._state_mutex:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
            tmp.replace(self.state_path)

    @staticmethod
    def _turn_session(turn_id):
        return "scorp-p0-turn-" + _sha(turn_id)[:16]

    @staticmethod
    def _conversation_session(url):
        return "scorp-p0-conv-" + _sha(url)[:16]

    @staticmethod
    def _now():
        return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalise_role(role):
        value = str(role or "UNKNOWN").strip().upper()
        if value not in _LIFECYCLE_ROLES:
            raise ValueError("CHROME_USE_SESSION_ROLE_INVALID")
        return value

    def _touch_session(self, state, session, *, role="UNKNOWN", turn_id=None, conversation_url=None):
        """Record ownership without changing the Chrome Use session itself."""

        session = str(session or "").strip()
        if not session:
            raise ValueError("CHROME_USE_SESSION_ID_MISSING")
        role = self._normalise_role(role)
        now = self._now()
        row = state["sessions"].get(session)
        if not isinstance(row, dict):
            row = {
                "session": session,
                "role": role,
                "status": "ACTIVE",
                "turn_ids": [],
                "conversation_urls": [],
                "created_at": now,
                "updated_at": now,
            }
        elif row.get("status") == "RETIRED":
            raise ValueError("CHROME_USE_SESSION_RETIRED")
        if row.get("role") in (None, "UNKNOWN") and role != "UNKNOWN":
            row["role"] = role
        elif role != "UNKNOWN" and row.get("role") not in (role, "UNKNOWN"):
            raise ValueError("CHROME_USE_SESSION_ROLE_CONFLICT")
        if turn_id is not None and str(turn_id) not in row.setdefault("turn_ids", []):
            row["turn_ids"].append(str(turn_id))
        if conversation_url is not None and str(conversation_url) not in row.setdefault("conversation_urls", []):
            row["conversation_urls"].append(str(conversation_url))
        row["updated_at"] = now
        state["sessions"][session] = row
        return row

    def bind_turn(self, turn_id, conversation_url, *, actor_kind=None):
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        role = self._normalise_role(actor_kind or "UNKNOWN")
        with self._state_mutex:
            state = self._load()
            old = state["turns"].get(turn_id)
            if conversation_url is None:
                if isinstance(old, dict) and old.get("session"):
                    self._touch_session(state, old["session"], role=role, turn_id=turn_id)
                    old["role"] = role if role != "UNKNOWN" else old.get("role", "UNKNOWN")
                    old.setdefault("status", "ACTIVE")
                    self._save(state)
                    return old["session"]
                session = self._turn_session(turn_id)
                state["turns"][turn_id] = {
                    "session": session,
                    "conversation_url": None,
                    "role": role,
                    "status": "ACTIVE",
                    "browser_io_started": False,
                }
                self._touch_session(state, session, role=role, turn_id=turn_id)
                self._save(state)
                return session
            url = _canonical_url(conversation_url)
            entry = state["conversations"].get(url)
            session = entry.get("session") if isinstance(entry, dict) else None
            if not session:
                session = self._conversation_session(url)
                state["conversations"][url] = {"session": session}
            prior_io = (
                old.get("browser_io_started")
                if isinstance(old, dict) and "browser_io_started" in old
                else False
            )
            state["turns"][turn_id] = {
                "session": session,
                "conversation_url": url,
                "role": role,
                "status": "ACTIVE",
                "browser_io_started": prior_io,
            }
            self._touch_session(state, session, role=role, turn_id=turn_id, conversation_url=url)
            self._save(state)
            return session

    def register_session(self, session, *, role="DIAGNOSTIC", turn_id=None):
        """Register an explicitly named session for lifecycle-managed cleanup."""

        with self._state_mutex:
            state = self._load()
            self._touch_session(state, session, role=role, turn_id=turn_id)
            self._save(state)
        return str(session)

    def lifecycle_snapshot(self):
        """Return lifecycle metadata without probing or mutating Chrome."""

        with self._state_mutex:
            state = self._load()
            return json.loads(json.dumps(state.get("sessions", {}), ensure_ascii=False))

    def _active_turns_for_session(self, state, session):
        return [
            turn_id
            for turn_id, row in state.get("turns", {}).items()
            if isinstance(row, dict)
            and row.get("session") == session
            and row.get("status", "ACTIVE") == "ACTIVE"
        ]

    async def retire_turn(self, turn_id, *, reason, stop=False, allow_persistent=False):
        """Retire one logical turn; stop Chrome only when its session is unreferenced."""

        turn_id = str(turn_id or "").strip()
        reason = str(reason or "").strip()
        if not turn_id or not reason:
            raise ValueError("CHROME_USE_RETIRE_ARGUMENT_MISSING")
        with self._state_mutex:
            state = self._load()
            row = state["turns"].get(turn_id)
            if not isinstance(row, dict) or not row.get("session"):
                raise ValueError("CHROME_USE_TURN_BINDING_MISSING")
            session = str(row["session"])
            role = self._normalise_role(row.get("role", "UNKNOWN"))
            if row.get("status") == "RETIRED":
                return {
                    "status": "ALREADY_RETIRED",
                    "session": session,
                    "active_turn_ids": self._active_turns_for_session(state, session),
                    "cleanup": "ALREADY_RETIRED",
                }
            if stop and role in {"MASTER", "WORKER"} and not allow_persistent:
                raise ValueError("CHROME_USE_PERSISTENT_SESSION_REFUSED")
            row["status"] = "RETIRED"
            row["retired_at"] = self._now()
            row["retired_reason"] = reason
            state["turns"][turn_id] = row
            active = self._active_turns_for_session(state, session)
            lifecycle = state["sessions"].get(session) or self._touch_session(state, session, role=role)
            lifecycle["updated_at"] = self._now()
            lifecycle["active_turn_ids"] = active
            if not active:
                # Retiring an assignment is not the same as retiring a
                # persistent Master/Worker slot.  Keep those sessions ACTIVE
                # for later slot reuse unless the caller explicitly requests
                # a stop and has opted into persistent cleanup.
                if stop:
                    lifecycle["status"] = "RETIRED"
                    lifecycle["retired_at"] = self._now()
                    lifecycle["retired_reason"] = reason
                else:
                    lifecycle["status"] = "ACTIVE"
            self._save(state)
        cleanup = "NOT_REQUESTED"
        if stop and not active:
            try:
                await self.cli.run_json(session, "session", "stop", timeout_seconds=self.timeout_seconds)
                cleanup = "STOPPED"
            except Exception as exc:
                cleanup = "CLEANUP_BLOCKED"
                with self._state_mutex:
                    state = self._load()
                    lifecycle = state["sessions"].setdefault(session, {})
                    lifecycle["cleanup_status"] = cleanup
                    lifecycle["cleanup_error_type"] = type(exc).__name__
                    lifecycle["cleanup_error"] = str(exc)
                    self._save(state)
        return {"status": "RETIRED", "session": session, "active_turn_ids": active, "cleanup": cleanup}

    async def retire_session(self, session, *, reason, stop=False, allow_persistent=False):
        """Retire a temporary session; persistent Master/Worker sessions are fenced by default."""

        session = str(session or "").strip()
        reason = str(reason or "").strip()
        if not session or not reason:
            raise ValueError("CHROME_USE_RETIRE_ARGUMENT_MISSING")
        with self._state_mutex:
            state = self._load()
            row = state["sessions"].get(session)
            if not isinstance(row, dict):
                raise ValueError("CHROME_USE_SESSION_UNKNOWN")
            role = self._normalise_role(row.get("role", "UNKNOWN"))
            if role in {"MASTER", "WORKER"} and not allow_persistent:
                raise ValueError("CHROME_USE_PERSISTENT_SESSION_REFUSED")
            if row.get("status") == "RETIRED":
                return {"status": "ALREADY_RETIRED", "session": session, "cleanup": "ALREADY_RETIRED"}
            active = self._active_turns_for_session(state, session)
            if active:
                raise ValueError("CHROME_USE_SESSION_HAS_ACTIVE_TURNS")
            row["status"] = "RETIRED"
            row["retired_at"] = self._now()
            row["retired_reason"] = reason
            state["sessions"][session] = row
            self._save(state)
        cleanup = "NOT_REQUESTED"
        if stop:
            try:
                await self.cli.run_json(session, "session", "stop", timeout_seconds=self.timeout_seconds)
                cleanup = "STOPPED"
            except Exception as exc:
                cleanup = "CLEANUP_BLOCKED"
                with self._state_mutex:
                    state = self._load()
                    row = state["sessions"].setdefault(session, {})
                    row["cleanup_status"] = cleanup
                    row["cleanup_error_type"] = type(exc).__name__
                    row["cleanup_error"] = str(exc)
                    self._save(state)
        return {"status": "RETIRED", "session": session, "cleanup": cleanup}

    def turn_binding(self, turn_id):
        with self._state_mutex:
            row = self._load()["turns"].get(str(turn_id or "").strip())
        return dict(row) if isinstance(row, dict) else None

    def _mark_turn_browser_io_started(self, turn_id):
        """Persist the exact transition immediately before first browser I/O."""

        turn = str(turn_id or "").strip()
        if not turn:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        with self._state_mutex:
            state = self._load()
            row = state["turns"].get(turn)
            if not isinstance(row, dict) or not row.get("session"):
                raise ValueError("ACTOR_GUI_TURN_BINDING_MISSING")
            row["browser_io_started"] = True
            row["browser_io_started_at"] = self._now()
            state["turns"][turn] = row
            self._save(state)
        return dict(row)

    def prove_turn_not_submitted(self, turn_id):
        """Return positive pre-I/O proof only from an existing durable driver state.

        bind_turn persists the turn before the first browser/navigation call.
        Therefore an existing, valid driver state that lacks this turn proves
        that this driver never crossed the browser-I/O boundary for it. A
        missing state file is not proof and deliberately remains ambiguous.
        """

        turn = str(turn_id or "").strip()
        if not turn:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        with self._state_mutex:
            if not self.state_path.is_file():
                return None
            state = self._load()
            row = state["turns"].get(turn)
            if isinstance(row, dict):
                if row.get("browser_io_started") is False:
                    return {
                        "proof": "PERSISTED_TURN_BINDING_BROWSER_IO_NOT_STARTED",
                        "protocol_version": str(state.get("protocol_version") or ""),
                        "known_turn_count": len(state["turns"]),
                        "session": str(row.get("session") or ""),
                    }
                return None
            return {
                "proof": "PERSISTED_DRIVER_STATE_NO_TURN_BINDING_BEFORE_BROWSER_IO",
                "protocol_version": str(state.get("protocol_version") or ""),
                "known_turn_count": len(state["turns"]),
            }

    def _session_for_url(self, url):
        with self._state_mutex:
            state = self._load()
            row = state["conversations"].get(url)
            if isinstance(row, dict) and row.get("session"):
                return row["session"]
            session = self._conversation_session(url)
            state["conversations"][url] = {"session": session}
            self._save(state)
            return session

    def _promote(self, turn_id, url):
        url = _canonical_url(url)
        with self._state_mutex:
            state = self._load()
            row = state["turns"].get(turn_id)
            if not isinstance(row, dict) or not row.get("session"):
                raise ValueError("CHROME_USE_TURN_BINDING_MISSING")
            session = row["session"]
            existing = state["conversations"].get(url)
            if isinstance(existing, dict) and existing.get("session") not in {None, session}:
                raise ValueError("CHROME_USE_CONVERSATION_SESSION_CONFLICT")
            row["conversation_url"] = url
            state["turns"][turn_id] = row
            state["conversations"][url] = {"session": session}
            self._touch_session(
                state,
                session,
                role=row.get("role", "UNKNOWN"),
                turn_id=turn_id,
                conversation_url=url,
            )
            self._save(state)
            return session

    async def _get_url(self, session, *, timeout_seconds=None):
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        payload = await self.cli.run_json(session, "get", "url", timeout_seconds=timeout)
        found = _extract_scalar(payload)
        if not found:
            raise ValueError("CHROME_USE_URL_MISSING")
        return _canonical_url(found, allow_root=True)

    async def _ensure_url(self, session, target):
        try:
            observed = await self._get_url(session)
        except RuntimeError:
            observed = None
        except ValueError as exc:
            if str(exc) not in {"CONVERSATION_URL_INVALID", "CHROME_USE_URL_MISSING"}:
                raise
            observed = None
        if observed != target:
            await self.cli.run_json(session, "open", target, timeout_seconds=self.timeout_seconds)
            observed = await self._get_url(session)
        if observed != target:
            raise ValueError("ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH")
        return observed

    async def _snapshot(self, session, url):
        await self._prepare_snapshot(session)
        payload = await self.cli.run_json(session, "read", timeout_seconds=self.timeout_seconds)
        return "Focused Window: Chrome\n" + url + "\n" + _render_payload(payload)

    def _existing_physical_binding(self, channel):
        """Resolve one durable actor session without creating or adopting state."""

        channel = str(channel or "").strip().casefold()
        if not channel:
            raise ValueError("CHROME_USE_CHANNEL_MISSING")
        role = "MASTER" if channel == "master" else "WORKER" if channel.startswith("worker") else None
        if role is None:
            raise ValueError("CHROME_USE_CHANNEL_UNSUPPORTED")
        with self._state_mutex:
            state = self._load()
            candidates = []
            for session, row in state.get("sessions", {}).items():
                if not isinstance(row, dict) or row.get("status") != "ACTIVE":
                    continue
                if self._normalise_role(row.get("role", "UNKNOWN")) != role:
                    continue
                urls = sorted({str(value or "").strip() for value in row.get("conversation_urls", []) if str(value or "").strip()})
                if len(urls) != 1:
                    continue
                candidates.append((str(session), urls[0]))
        if len(candidates) != 1:
            raise ValueError(f"CHROME_USE_PHYSICAL_BINDING_COUNT_{len(candidates)}")
        return candidates[0]

    async def observe_current_binding(self, channel):
        """Observe driver and physical browser identity without navigation or focus."""

        session, driver_url = self._existing_physical_binding(channel)
        physical_url = await self._get_url(session)
        if physical_url == _ROOT_URL:
            raise ValueError("CHROME_USE_PHYSICAL_CONVERSATION_MISSING")
        payload = await self.cli.run_json(session, "read", timeout_seconds=self.timeout_seconds)
        return {
            "driver_url": driver_url,
            "physical_url": physical_url,
            "snapshot": "Focused Window: Chrome\n" + physical_url + "\n" + _render_payload(payload),
            "session": session,
        }

    async def _prepare_interactive(self, session, *, timeout_seconds=None):
        """Request foreground rendering when the transport supports it.

        The driver is also used with deterministic fake clients in offline
        tests. Only the Chrome Use adapter advertises this capability, so tests
        and alternate transports retain their existing call contract.
        """

        prepare = getattr(self.cli, "prepare_interactive", None)
        if callable(prepare):
            timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
            await prepare(session, timeout_seconds=timeout)

    async def _prepare_snapshot(self, session, *, timeout_seconds=None):
        """Prepare a read snapshot without foregrounding real Chrome by default.

        Older injected test/legacy clients do not expose the Chrome Use
        capability object and historically expected a foreground command at
        this boundary. Preserve that compatibility seam while the real
        ``ChromeUseCliV3(interactive=False)`` path stays background-only.
        """

        prepare = getattr(self.cli, "prepare_interactive", None)
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if callable(prepare):
            await prepare(session, timeout_seconds=timeout)
            return
        if getattr(self.cli, "interactive", True):
            await self.cli.run_json(session, "bringToFront", timeout_seconds=timeout)

    async def _editor_ref(self, session, *, allow_rate_limit_recovery=True):
        await self._prepare_interactive(session)
        # A newly selected ChatGPT tab can expose a transient accessibility
        # tree with no (or multiple) textbox refs while React finishes
        # rendering.  Re-read only; no browser side effect is allowed in this
        # recovery.  If the tree remains ambiguous, preserve the original
        # fail-closed error.
        for attempt in range(3):
            payload = await self.cli.run_json(session, "snapshot", "-i", timeout_seconds=self.timeout_seconds)
            if allow_rate_limit_recovery and chatgpt_throttle_visible(_render_payload(payload)):
                recovery = await self.recover_rate_limit_dialog(session)
                if recovery.get("status") != "RECOVERED":
                    reason = str(recovery.get("reason") or "UNKNOWN")
                    raise ValueError(f"CHROME_USE_RATE_LIMIT_RECOVERY_BLOCKED:{reason}")
                # The recovery path may refresh the page, so resolve a fresh
                # textbox ref and never reuse a pre-refresh accessibility target.
                return await self._editor_ref(session, allow_rate_limit_recovery=False)
            try:
                return _editor_ref_from_snapshot(payload)
            except ValueError as exc:
                if not str(exc).startswith("CHROME_USE_EDITOR_REF_COUNT_") or attempt >= 2:
                    raise
                await self.sleeper(0.1)
        raise AssertionError("unreachable")

    async def _send_ref(self, session, *, expected_prompt=None):
        await self._prepare_interactive(session)
        payload = await self.cli.run_json(session, "snapshot", "-i", timeout_seconds=self.timeout_seconds)
        if expected_prompt is not None:
            observation = _prompt_observation_from_snapshot(payload, expected_prompt)
            if observation == "MISMATCH":
                raise ValueError("CHROME_USE_PROMPT_NOT_CONFIRMED")
        try:
            return _send_ref_from_snapshot(payload)
        except ValueError as exc:
            if str(exc).startswith("CHROME_USE_SEND_REF_COUNT_"):
                raise SendControlResolutionError(str(exc), payload) from exc
            raise

    async def recover_rate_limit_dialog(self, session, *, max_wait_seconds=300, poll_seconds=5):
        """Acknowledge one known rate-limit dialog and wait for page recovery.

        This is an explicit recovery operation.  It never enters a prompt, sends
        a message, or retries an ambiguous external action.  It waits for the
        configured recovery window, then performs at most one explicit page
        reload before the final read-only check.  A missing or ambiguous
        acknowledgement control remains blocked.
        """

        session = str(session or "").strip()
        if not session:
            raise ValueError("CHROME_USE_SESSION_MISSING")
        max_wait = float(max_wait_seconds)
        poll = float(poll_seconds)
        if max_wait < 0 or poll <= 0:
            raise ValueError("CHROME_USE_RATE_LIMIT_WAIT_INVALID")

        async def read_snapshot():
            await self._prepare_interactive(session)
            return await self.cli.run_json(
                session,
                "snapshot",
                "-i",
                timeout_seconds=self.timeout_seconds,
            )

        try:
            payload = await read_snapshot()
        except Exception as exc:
            return {
                "status": "BLOCKED",
                "reason": "RATE_LIMIT_INITIAL_READ_FAILED",
                "error": str(exc),
            }
        text = _render_payload(payload)
        if not chatgpt_throttle_visible(text):
            return {
                "status": "NOT_RATE_LIMITED",
                "reason": "RATE_LIMIT_DIALOG_NOT_VISIBLE",
                "snapshot_sha256": _sha(text),
            }
        try:
            ack_ref = _rate_limit_ack_ref_from_snapshot(payload)
        except (ValueError, TypeError) as exc:
            return {
                "status": "BLOCKED",
                "reason": "RATE_LIMIT_ACK_UNRESOLVED",
                "error": str(exc),
                "snapshot_sha256": _sha(text),
            }
        ambiguous_ack = False
        try:
            await self.cli.run_json(session, "click", ack_ref, timeout_seconds=self.timeout_seconds)
        except Exception as exc:
            # A transport timeout is not proof that the acknowledgement did
            # not reach the page.  Reconcile the same physical actor with a
            # read-only snapshot before deciding whether recovery can continue.
            # Other click failures are deterministic failures and remain
            # blocked without an extra browser operation.
            error_text = str(exc or "").casefold()
            if not any(marker in error_text for marker in ("timeout", "timed out", "deadline", "eof")):
                return {
                    "status": "BLOCKED",
                    "reason": "RATE_LIMIT_ACK_FAILED",
                    "error": str(exc),
                    "snapshot_sha256": _sha(text),
                }
            ambiguous_ack = True
            try:
                payload = await read_snapshot()
            except Exception as reconcile_exc:
                return {
                    "status": "BLOCKED",
                    "reason": "RATE_LIMIT_ACK_AMBIGUOUS_READ_FAILED",
                    "error": str(reconcile_exc),
                    "click_error": str(exc),
                    "snapshot_sha256": _sha(text),
                }
            text = _render_payload(payload)
            if chatgpt_throttle_visible(text):
                return {
                    "status": "BLOCKED",
                    "reason": "RATE_LIMIT_ACK_AMBIGUOUS",
                    "click_error": str(exc),
                    "snapshot_sha256": _sha(text),
                }
            classification = classify_chatgpt_snapshot(text, session)
            if classification["status"] in {"AUTHENTICATION_REQUIRED", "CAPTCHA_REQUIRED"}:
                return {
                    "status": "BLOCKED",
                    "reason": "RATE_LIMIT_RECOVERY_REQUIRES_USER_AUTH",
                    "page_status": classification["status"],
                    "snapshot_sha256": _sha(text),
                }
            if classification["status"] == "AUTHENTICATED" and _composer_ready_or_unreported(payload):
                return {
                    "status": "RECOVERED",
                    "reason": "RATE_LIMIT_DIALOG_CLEARED_AFTER_AMBIGUOUS_ACK",
                    "snapshot_sha256": _sha(text),
                }

        deadline = self.clock() + max_wait
        while True:
            try:
                payload = await read_snapshot()
            except Exception as exc:
                return {
                    "status": "BLOCKED",
                    "reason": "RATE_LIMIT_RECOVERY_READ_FAILED",
                    "error": str(exc),
                    "snapshot_sha256": _sha(text),
                }
            text = _render_payload(payload)
            classification = classify_chatgpt_snapshot(text, session)
            if classification["status"] == "AUTHENTICATED":
                # A dismissed dialog can leave the authenticated page in a
                # reload/layout window with no usable composer yet.  Do not
                # report recovery until the unique textbox is present; the
                # caller will otherwise immediately fail on a stale/empty
                # accessibility tree and may incorrectly treat the page as
                # ready for a new attempt.
                if _composer_ready_or_unreported(payload):
                    return {
                        "status": "RECOVERED",
                        "reason": (
                            "RATE_LIMIT_DIALOG_CLEARED_AFTER_AMBIGUOUS_ACK"
                            if ambiguous_ack
                            else "RATE_LIMIT_DIALOG_CLEARED"
                        ),
                        "snapshot_sha256": _sha(text),
                    }
            if classification["status"] in {"AUTHENTICATION_REQUIRED", "CAPTCHA_REQUIRED"}:
                return {
                    "status": "BLOCKED",
                    "reason": "RATE_LIMIT_RECOVERY_REQUIRES_USER_AUTH",
                    "page_status": classification["status"],
                    "snapshot_sha256": _sha(text),
                }
            now = self.clock()
            if now >= deadline:
                try:
                    await self.cli.run_json(
                        session,
                        "reload",
                        timeout_seconds=self.timeout_seconds,
                    )
                    payload = await read_snapshot()
                    text = _render_payload(payload)
                    classification = classify_chatgpt_snapshot(text, session)
                    if classification["status"] == "AUTHENTICATED":
                        if _composer_ready_or_unreported(payload):
                            return {
                                "status": "RECOVERED",
                                "reason": (
                                    "RATE_LIMIT_DIALOG_CLEARED_AFTER_AMBIGUOUS_ACK_AFTER_REFRESH"
                                    if ambiguous_ack
                                    else "RATE_LIMIT_DIALOG_CLEARED_AFTER_REFRESH"
                                ),
                                "snapshot_sha256": _sha(text),
                            }
                    if classification["status"] in {"AUTHENTICATION_REQUIRED", "CAPTCHA_REQUIRED"}:
                        return {
                            "status": "BLOCKED",
                            "reason": "RATE_LIMIT_RECOVERY_REQUIRES_USER_AUTH",
                            "page_status": classification["status"],
                            "snapshot_sha256": _sha(text),
                        }
                    return {
                        "status": "BLOCKED",
                        "reason": "RATE_LIMIT_RECOVERY_TIMEOUT",
                        "snapshot_sha256": _sha(text),
                    }
                except Exception as exc:
                    return {
                        "status": "BLOCKED",
                        "reason": "RATE_LIMIT_REFRESH_FAILED",
                        "error": str(exc),
                        "snapshot_sha256": _sha(text),
                    }
            await self.sleeper(min(poll, max(0.0, deadline - now)))

    async def _send_ref_after_input_repair(self, session, editor_ref, prompt):
        """Resolve Send after a native fill that did not activate React state.

        ChatGPT's composer can remain in its empty controlled-input state after
        a browser-native fill.  In that state the Send control is absent from
        the accessibility tree even though the CLI reports a successful fill.
        A key-event type with ``--clear`` repairs that same unsent intent.  It
        is deliberately skipped when a Stop-generating control is visible,
        because that indicates an active response rather than an empty
        composer and typing again could corrupt the session.
        """
        try:
            return await self._send_ref(session, expected_prompt=prompt)
        except SendControlResolutionError as first_error:
            if chatgpt_throttle_visible(_render_payload(first_error.payload)):
                recovery = await self.recover_rate_limit_dialog(session)
                if recovery.get("status") != "RECOVERED":
                    first_error.add_context(
                        key_event_repair="SKIPPED_RATE_LIMIT",
                        rate_limit_recovery=dict(recovery),
                    )
                    raise first_error
                try:
                    # A recovery refresh can discard the unsent composer.  Fill
                    # the exact same intent into a fresh textbox ref before
                    # resolving Send; never use key-event repair on the dialog.
                    recovered_editor_ref = await self._editor_ref(
                        session,
                        allow_rate_limit_recovery=False,
                    )
                    await self.cli.run_json(
                        session,
                        "fill",
                        recovered_editor_ref,
                        prompt,
                        timeout_seconds=self.timeout_seconds,
                    )
                    return await self._send_ref(session, expected_prompt=prompt)
                except SendControlResolutionError as recovered_error:
                    recovered_error.add_context(
                        rate_limit_recovery="RECOVERED_BUT_SEND_CONTROL_UNRESOLVED",
                        initial_snapshot_sha256=first_error.diagnostics.get("snapshot_sha256"),
                    )
                    raise
                except Exception as exc:
                    first_error.add_context(
                        rate_limit_recovery="RECOVERED_BUT_REFILL_FAILED",
                        rate_limit_recovery_error=type(exc).__name__,
                    )
                    raise first_error from exc
            labels = {
                str(value or "").casefold()
                for value in first_error.diagnostics.get("button_names", [])
            }
            if any(
                marker in label
                for label in labels
                for marker in ("stop generating", "停止生成", "停止回答")
            ):
                first_error.add_context(key_event_repair="SKIPPED_ACTIVE_GENERATION")
                raise
            try:
                # Chrome Use remints accessibility refs on every snapshot. The
                # editor ref captured before fill is therefore not safe to
                # reuse after this failed post-fill snapshot.
                repair_editor_ref = _editor_ref_from_snapshot(first_error.payload)
            except ValueError as exc:
                first_error.add_context(
                    key_event_repair="FAILED_FRESH_EDITOR_REF",
                    key_event_error=str(exc),
                )
                raise first_error from exc
            try:
                if "\n" in prompt or "\r" in prompt:
                    # Key-event typing interprets a newline as Enter in the
                    # ChatGPT composer.  That can submit only the first line
                    # and leave the remainder as a second unsent draft.  The
                    # Chrome Use paste command preserves newlines and still
                    # targets the freshly minted textbox ref.
                    await self.cli.run_json(
                        session,
                        "paste",
                        prompt,
                        "--selector",
                        repair_editor_ref,
                        timeout_seconds=self.timeout_seconds,
                    )
                else:
                    await self.cli.run_json(
                        session,
                        "type",
                        repair_editor_ref,
                        prompt,
                        "--key-events",
                        "--clear",
                        timeout_seconds=self.timeout_seconds,
                    )
            except Exception as exc:
                first_error.add_context(
                    key_event_repair="FAILED",
                    key_event_error=type(exc).__name__,
                )
                raise first_error from exc
            try:
                return await self._send_ref(session, expected_prompt=prompt)
            except SendControlResolutionError as repaired_error:
                repaired_error.add_context(
                    key_event_repair="USED_BUT_SEND_CONTROL_STILL_MISSING",
                    initial_snapshot_sha256=first_error.diagnostics.get("snapshot_sha256"),
                    initial_button_names=first_error.diagnostics.get("button_names", []),
                )
                raise

    async def _fill_prompt_with_reconciliation(self, session, editor_ref, prompt, target):
        """Fill once, and recover one daemon EOF only after a read-only check.

        A Chrome Use daemon EOF does not prove whether a composer fill reached
        the page.  Because this operation is pre-submit, a read-only snapshot
        can safely decide whether the exact prompt is already present.  Only
        when it is absent do we resolve a fresh textbox ref and retry fill once.
        This helper is never used for click, Enter, or any message submission.
        """

        try:
            await self.cli.run_json(
                session,
                "fill",
                editor_ref,
                prompt,
                timeout_seconds=self.timeout_seconds,
            )
            return editor_ref
        except RuntimeError as exc:
            if not _chrome_use_daemon_busy_error(exc):
                raise
            try:
                observed = await self._snapshot(session, target)
                if str(prompt) in observed:
                    return editor_ref
                fresh_editor_ref = await self._editor_ref(session)
                await self.cli.run_json(
                    session,
                    "fill",
                    fresh_editor_ref,
                    prompt,
                    timeout_seconds=self.timeout_seconds,
                )
                return fresh_editor_ref
            except Exception as recovery_error:
                raise exc from recovery_error

    async def _recover_new_conversation_after_timeout(self, session, turn_id, original_error):
        """Reconcile one possibly completed new-chat submission without replaying it."""

        recovery_timeout = min(float(self.timeout_seconds), 5.0)
        for attempt in range(4):
            observed_after_timeout = None
            try:
                observed_after_timeout = await self._get_url(
                    session,
                    timeout_seconds=recovery_timeout,
                )
            except TimeoutError:
                pass
            if observed_after_timeout is not None and observed_after_timeout != _ROOT_URL:
                observed_after_timeout = _canonical_url(observed_after_timeout)
                self._promote(turn_id, observed_after_timeout)
                return await self._snapshot(session, observed_after_timeout)
            if attempt < 3:
                await self.sleeper(0.5)
        raise original_error

    async def snapshot_conversation(self, conversation_url, *, window_handle=None):
        if window_handle is not None:
            raise ValueError("CHROME_USE_WINDOW_HANDLE_UNSUPPORTED")
        url = _canonical_url(conversation_url)
        session = self._session_for_url(url)
        await self._ensure_url(session, url)
        return await self._snapshot(session, url)

    async def recover_persisted_turn_snapshot(self, turn_id, *, timeout_seconds=None):
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        binding = self.turn_binding(turn_id)
        if not isinstance(binding, dict):
            raise ValueError("ACTOR_GUI_RECOVERY_BINDING_MISSING")
        session = str(binding.get("session") or "").strip()
        conversation_url = str(binding.get("conversation_url") or "").strip()
        if not session or not conversation_url:
            raise ValueError("ACTOR_GUI_RECOVERY_BINDING_MISSING")
        url = _canonical_url(conversation_url)
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("CHROME_USE_TIMEOUT_INVALID")
        observed = await self._get_url(session, timeout_seconds=timeout)
        if observed != url:
            raise ValueError("ACTOR_GUI_RECOVERY_CONVERSATION_MISMATCH")
        await self._prepare_snapshot(session, timeout_seconds=timeout)
        payload = await self.cli.run_json(session, "read", timeout_seconds=timeout)
        return "Focused Window: Chrome\n" + url + "\n" + _render_payload(payload)

    async def recover_unpromoted_turn_snapshot(self, turn_id, *, expected_marker, timeout_seconds=None):
        turn_id = str(turn_id or "").strip()
        marker = str(expected_marker or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        if not marker:
            raise ValueError("ACTOR_GUI_RECOVERY_MARKER_MISSING")
        binding = self.turn_binding(turn_id)
        if not isinstance(binding, dict):
            raise ValueError("ACTOR_GUI_RECOVERY_BINDING_MISSING")
        session = str(binding.get("session") or "").strip()
        if not session:
            raise ValueError("ACTOR_GUI_RECOVERY_BINDING_MISSING")
        if str(binding.get("conversation_url") or "").strip():
            return await self.recover_persisted_turn_snapshot(turn_id, timeout_seconds=timeout_seconds)
        timeout = self.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("CHROME_USE_TIMEOUT_INVALID")
        observed = await self._get_url(session, timeout_seconds=timeout)
        if observed == _ROOT_URL:
            raise ValueError("ACTOR_GUI_RECOVERY_CANONICAL_URL_MISSING")
        observed = _canonical_url(observed)
        await self._prepare_snapshot(session, timeout_seconds=timeout)
        payload = await self.cli.run_json(session, "read", timeout_seconds=timeout)
        snapshot = "Focused Window: Chrome\n" + observed + "\n" + _render_payload(payload)
        if marker not in snapshot:
            raise ValueError("ACTOR_GUI_RECOVERY_MARKER_MISSING")
        self._promote(turn_id, observed)
        return snapshot

    async def submit_prompt(self, *, prompt, turn_id, actor_kind, conversation_url):
        # Persist the logical turn before waiting on the process-local submit
        # mutex.  A timeout here is therefore recoverable with positive
        # evidence that this turn never crossed the browser-I/O boundary.
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("ACTOR_GUI_PROMPT_MISSING")
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        actor_kind = str(actor_kind or "").strip().upper()
        if actor_kind not in _ALLOWED_ACTORS:
            raise ValueError("ACTOR_GUI_ACTOR_KIND_INVALID")
        self.bind_turn(turn_id, conversation_url, actor_kind=actor_kind)
        acquired = await asyncio.to_thread(
            self._submission_mutex.acquire,
            True,
            max(1.0, float(self.timeout_seconds)),
        )
        if not acquired:
            raise TimeoutError("CHROME_USE_SUBMISSION_BUSY")
        try:
            return await self._submit_prompt_unlocked(
                prompt=prompt,
                turn_id=turn_id,
                actor_kind=actor_kind,
                conversation_url=conversation_url,
            )
        finally:
            self._submission_mutex.release()

    async def _submit_prompt_unlocked(self, *, prompt, turn_id, actor_kind, conversation_url):
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("ACTOR_GUI_PROMPT_MISSING")
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        actor_kind = str(actor_kind or "").strip().upper()
        if actor_kind not in _ALLOWED_ACTORS:
            raise ValueError("ACTOR_GUI_ACTOR_KIND_INVALID")
        session = self.bind_turn(turn_id, conversation_url, actor_kind=actor_kind)
        # This durable edge is the exact point after which transport failure is
        # ambiguous.  Nothing above it performs browser/navigation I/O.
        self._mark_turn_browser_io_started(turn_id)
        target = _canonical_url(conversation_url) if conversation_url is not None else _ROOT_URL
        if conversation_url is None:
            open_new_tab = getattr(self.cli, "open_new_tab", None)
            if callable(open_new_tab):
                await open_new_tab(session, target, timeout_seconds=self.timeout_seconds)
            else:
                # Alternate/fake transports may not expose tab ownership;
                # retain their existing navigation contract.
                await self.cli.run_json(session, "open", target, timeout_seconds=self.timeout_seconds)
            observed_root = await self._get_url(session)
            if observed_root != target:
                raise ValueError("ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH")
        else:
            await self._ensure_url(session, target)
        editor_ref = await self._editor_ref(session)
        editor_ref = await self._fill_prompt_with_reconciliation(
            session,
            editor_ref,
            prompt,
            target,
        )
        try:
            send_ref = await self._send_ref_after_input_repair(session, editor_ref, prompt)
        except SendControlResolutionError as exc:
            exc.add_context(
                editor_ref=editor_ref,
                prompt_length=len(prompt),
                prompt_sha256=_sha(prompt),
            )
            if exc.diagnostics.get("key_event_repair") != "USED_BUT_SEND_CONTROL_STILL_MISSING":
                raise
            try:
                # The first key-event repair only restores the controlled
                # editor state.  If the accessibility tree still has no Send
                # control, submit the same intent once with the CLI's explicit
                # Enter command.  A later URL check decides whether it
                # actually created a conversation; there is no blind retry.
                await self.cli.run_json(
                    session,
                    "press",
                    "Enter",
                    "--selector",
                    editor_ref,
                    timeout_seconds=self.timeout_seconds,
                )
            except TimeoutError as timeout_error:
                if conversation_url is None:
                    return await self._recover_new_conversation_after_timeout(
                        session, turn_id, timeout_error
                    )
                raise
            except Exception as fallback_error:
                exc.add_context(
                    key_event_submit="FAILED",
                    key_event_submit_error=type(fallback_error).__name__,
                )
                raise exc from fallback_error
            send_ref = None
        if send_ref is not None:
            try:
                await self.cli.run_json(session, "click", send_ref, timeout_seconds=self.timeout_seconds)
            except TimeoutError as timeout_error:
                if conversation_url is not None:
                    raise
                return await self._recover_new_conversation_after_timeout(
                    session, turn_id, timeout_error
                )
        observed = target
        for _ in range(20):
            try:
                observed = await self._get_url(session)
            except ValueError as exc:
                # Chrome Use can expose a transient about:blank or WEB:
                # placeholder while the new ChatGPT conversation is being
                # promoted. It is not a valid identity and must never be
                # persisted; continue polling for a canonical /c/<id> URL.
                if str(exc) not in {"CONVERSATION_URL_INVALID", "CHROME_USE_URL_MISSING"}:
                    raise
                observed = None
            if conversation_url is not None:
                if observed != target:
                    raise ValueError("ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH")
                break
            if observed and observed != _ROOT_URL:
                observed = _canonical_url(observed)
                break
            await self.sleeper(0.25)
        if not observed or observed == _ROOT_URL:
            raise TimeoutError("CHROME_USE_CONVERSATION_URL_TIMEOUT")
        self._promote(turn_id, observed)
        return await self._snapshot(session, observed)

    async def discover_turn_conversations(self, turn_id):
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        binding = self.turn_binding(turn_id)
        if binding is None:
            return []
        session = binding["session"]
        try:
            url = await self._get_url(session)
        except (RuntimeError, ValueError):
            return []
        if url == _ROOT_URL:
            return []
        snapshot = await self._snapshot(session, url)
        marker = f"SCORP_GUI_ACTOR_V3::{turn_id}::"
        if f"TURN_ID={turn_id}" not in snapshot and marker not in snapshot:
            return []
        self._promote(turn_id, url)
        return [{"conversation_url": url, "snapshot": snapshot}]
