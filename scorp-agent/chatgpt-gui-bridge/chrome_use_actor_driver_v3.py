from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from gui_transport import validate_conversation_url

_ROOT_URL = "https://chatgpt.com/"
_PROTOCOL = "scorp.chrome-use-driver/v1"
_ALLOWED_ACTORS = {"MASTER", "WORKER"}
_SEND_BUTTON_NAMES = {"发送提示词", "发送消息", "send prompt", "send message", "send"}


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
        name = " ".join(str(meta.get("name") or "").split()).casefold()
        if name in _SEND_BUTTON_NAMES:
            candidates.append(str(ref))
    candidates = sorted(set(candidates))
    if len(candidates) != 1:
        raise ValueError(f"CHROME_USE_SEND_REF_COUNT_{len(candidates)}")
    return "@" + candidates[0]


class ChromeUseActorDriverV3:
    def __init__(self, cli, state_path, *, sleeper=asyncio.sleep, timeout_seconds=30):
        self.cli = cli
        self.state_path = Path(state_path)
        self.sleeper = sleeper
        self.timeout_seconds = int(timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("CHROME_USE_DRIVER_TIMEOUT_INVALID")

    def _load(self):
        if not self.state_path.is_file():
            return {"protocol_version": _PROTOCOL, "turns": {}, "conversations": {}}
        value = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or value.get("protocol_version") != _PROTOCOL:
            raise ValueError("CHROME_USE_DRIVER_STATE_INVALID")
        if not isinstance(value.get("turns"), dict) or not isinstance(value.get("conversations"), dict):
            raise ValueError("CHROME_USE_DRIVER_STATE_INVALID")
        return value

    def _save(self, value):
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

    def bind_turn(self, turn_id, conversation_url):
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        state = self._load()
        old = state["turns"].get(turn_id)
        if conversation_url is None:
            if isinstance(old, dict) and old.get("session"):
                return old["session"]
            session = self._turn_session(turn_id)
            state["turns"][turn_id] = {"session": session, "conversation_url": None}
            self._save(state)
            return session
        url = _canonical_url(conversation_url)
        entry = state["conversations"].get(url)
        session = entry.get("session") if isinstance(entry, dict) else None
        if not session:
            session = self._conversation_session(url)
            state["conversations"][url] = {"session": session}
        state["turns"][turn_id] = {"session": session, "conversation_url": url}
        self._save(state)
        return session

    def turn_binding(self, turn_id):
        row = self._load()["turns"].get(str(turn_id or "").strip())
        return dict(row) if isinstance(row, dict) else None

    def _session_for_url(self, url):
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
        await self.cli.run_json(session, "bringToFront", timeout_seconds=self.timeout_seconds)
        payload = await self.cli.run_json(session, "read", timeout_seconds=self.timeout_seconds)
        return "Focused Window: Chrome\n" + url + "\n" + _render_payload(payload)

    async def _editor_ref(self, session):
        payload = await self.cli.run_json(session, "snapshot", "-i", timeout_seconds=self.timeout_seconds)
        return _editor_ref_from_snapshot(payload)

    async def _send_ref(self, session):
        payload = await self.cli.run_json(session, "snapshot", "-i", timeout_seconds=self.timeout_seconds)
        return _send_ref_from_snapshot(payload)

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
        await self.cli.run_json(session, "bringToFront", timeout_seconds=timeout)
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
        await self.cli.run_json(session, "bringToFront", timeout_seconds=timeout)
        payload = await self.cli.run_json(session, "read", timeout_seconds=timeout)
        snapshot = "Focused Window: Chrome\n" + observed + "\n" + _render_payload(payload)
        if marker not in snapshot:
            raise ValueError("ACTOR_GUI_RECOVERY_MARKER_MISSING")
        self._promote(turn_id, observed)
        return snapshot

    async def submit_prompt(self, *, prompt, turn_id, actor_kind, conversation_url):
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ValueError("ACTOR_GUI_PROMPT_MISSING")
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise ValueError("ACTOR_GUI_TURN_ID_MISSING")
        actor_kind = str(actor_kind or "").strip().upper()
        if actor_kind not in _ALLOWED_ACTORS:
            raise ValueError("ACTOR_GUI_ACTOR_KIND_INVALID")
        session = self.bind_turn(turn_id, conversation_url)
        target = _canonical_url(conversation_url) if conversation_url is not None else _ROOT_URL
        if conversation_url is None:
            await self.cli.run_json(session, "open", target, timeout_seconds=self.timeout_seconds)
            observed_root = await self._get_url(session)
            if observed_root != target:
                raise ValueError("ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH")
        else:
            await self._ensure_url(session, target)
        editor_ref = await self._editor_ref(session)
        await self.cli.run_json(session, "fill", editor_ref, prompt, timeout_seconds=self.timeout_seconds)
        send_ref = await self._send_ref(session)
        try:
            await self.cli.run_json(session, "click", send_ref, timeout_seconds=self.timeout_seconds)
        except TimeoutError:
            if conversation_url is not None:
                raise
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
            raise
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
