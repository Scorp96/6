from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

from gui_transport import ChatGptThrottleError


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def _text(value, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


def _retryable_pre_submit_reason(exc: Exception) -> str | None:
    message = str(exc or "").strip()
    if isinstance(exc, ChatGptThrottleError) and message == "CHATGPT_REQUEST_THROTTLED":
        return message
    if isinstance(exc, RuntimeError) and message == "CHATGPT_COMPOSER_UNAVAILABLE":
        return message
    return None


def _submit_error_diagnostic(exc: Exception, prompt: str) -> tuple[str, str, str]:
    error_type = type(exc).__name__
    message = str(exc or "").strip()
    if prompt:
        message = message.replace(prompt, "<redacted-prompt>")
    message = " ".join(message.split())[:1024]
    if not message:
        message = error_type
    occurred_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return error_type, message, occurred_at


class DurableActorTransportV3:
    """Crash-safe exactly-once submission ledger for actor GUI transports.

    A durable SUBMITTING intent is written before invoking the backend. If the
    process dies after the remote side effect but before the returned handle is
    persisted, restart uses backend.recover(submission_id) and never blindly
    submits the prompt a second time.

    Explicit pre-generation GUI failures are different: throttle/composer
    unavailability happens before a remote generation is established. Those
    rows become RETRYABLE so the deterministic supervisor can defer and later
    re-submit without entering recovery/ambiguity handling.
    """

    def __init__(self, path, backend):
        self.path = Path(path)
        self.backend = backend

    def _rows(self) -> dict:
        if not self.path.is_file():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("ACTOR_TRANSPORT_LEDGER_INVALID")
        return value

    def _binding(self, *, prompt, turn_id, actor_kind, conversation_url, timeout_seconds):
        turn_id = _text(turn_id, "ACTOR_TRANSPORT_TURN_ID_MISSING")
        prompt = _text(prompt, "ACTOR_TRANSPORT_PROMPT_MISSING")
        actor_kind = str(actor_kind or "").strip().upper()
        if actor_kind not in {"MASTER", "WORKER"}:
            raise ValueError("ACTOR_TRANSPORT_ACTOR_KIND_INVALID")
        if conversation_url is not None:
            conversation_url = _text(conversation_url, "ACTOR_TRANSPORT_CONVERSATION_URL_INVALID")
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds <= 0:
            raise ValueError("ACTOR_TRANSPORT_TIMEOUT_INVALID")
        value = {
            "turn_id": turn_id,
            "actor_kind": actor_kind,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "conversation_url": conversation_url,
            "timeout_seconds": timeout_seconds,
        }
        return value, prompt

    def _public(self, row: dict, status: str | None = None) -> dict:
        out = {
            "status": status or row["state"],
            "turn_id": row["turn_id"],
            "submission_id": row["submission_id"],
        }
        if isinstance(row.get("handle"), dict):
            out["handle"] = copy.deepcopy(row["handle"])
            if row["handle"].get("conversation_url"):
                out["conversation_url"] = row["handle"]["conversation_url"]
        if row.get("state") == "COMPLETED":
            out["response"] = copy.deepcopy(row["response"])
            out["snapshot"] = row.get("snapshot")
            out["conversation_url"] = row.get("conversation_url")
        if row.get("state") == "TIMED_OUT":
            out["reason"] = row.get("timeout_reason")
            out["timed_out_at"] = row.get("timed_out_at")
        if row.get("state") == "RETRYABLE":
            out["reason"] = row.get("retry_reason")
        return out

    async def _submit_backend(self, *, rows, row, binding, prompt, submission_id) -> dict:
        try:
            handle = await self.backend.submit(
                submission_id,
                prompt=prompt,
                turn_id=binding["turn_id"],
                actor_kind=binding["actor_kind"],
                conversation_url=binding["conversation_url"],
                timeout_seconds=binding["timeout_seconds"],
            )
        except Exception as exc:
            reason = _retryable_pre_submit_reason(exc)
            current_rows = self._rows()
            current = current_rows.get(binding["turn_id"])
            if (
                not isinstance(current, dict)
                or current.get("binding_sha256") != row.get("binding_sha256")
                or current.get("submission_id") != submission_id
                or current.get("state") != "SUBMITTING"
            ):
                if reason is not None:
                    raise ValueError("ACTOR_TRANSPORT_RETRYABLE_STATE_CONFLICT") from exc
                raise
            if reason is None:
                error_type, error_message, error_at = _submit_error_diagnostic(exc, prompt)
                current = {**current, "submit_error_type": error_type, "submit_error": error_message, "submit_error_at": error_at}
                current_rows[binding["turn_id"]] = current
                _atomic(self.path, current_rows)
                raise
            current = {**current, "state": "RETRYABLE", "retry_reason": reason}
            current_rows[binding["turn_id"]] = current
            _atomic(self.path, current_rows)
            return self._public(current, "DEFERRED")

        if not isinstance(handle, dict) or handle.get("submission_id") != submission_id:
            raise ValueError("ACTOR_TRANSPORT_SUBMIT_HANDLE_INVALID")
        current_rows = self._rows()
        current = current_rows.get(binding["turn_id"])
        if (
            not isinstance(current, dict)
            or current.get("binding_sha256") != row.get("binding_sha256")
            or current.get("submission_id") != submission_id
            or current.get("state") != "SUBMITTING"
        ):
            raise ValueError("ACTOR_TRANSPORT_SUBMIT_STATE_CONFLICT")
        current = {**current, "state": "SUBMITTED", "handle": copy.deepcopy(handle)}
        current.pop("retry_reason", None)
        current_rows[binding["turn_id"]] = current
        _atomic(self.path, current_rows)
        return self._public(current, "SUBMITTED")

    async def submit(self, *, prompt, turn_id, actor_kind, conversation_url=None, timeout_seconds=1800) -> dict:
        binding, prompt = self._binding(
            prompt=prompt,
            turn_id=turn_id,
            actor_kind=actor_kind,
            conversation_url=conversation_url,
            timeout_seconds=timeout_seconds,
        )
        binding_sha = _sha(binding)
        submission_id = "actor-submit-" + hashlib.sha256(("scorp.actor-transport/v3\n" + binding_sha).encode("utf-8")).hexdigest()
        rows = self._rows()
        row = rows.get(binding["turn_id"])

        if row is not None:
            if not isinstance(row, dict) or row.get("binding_sha256") != binding_sha or row.get("submission_id") != submission_id:
                raise ValueError("ACTOR_TRANSPORT_BINDING_CONFLICT")
            state = row.get("state")
            if state == "SUBMITTED":
                return self._public(row, "ALREADY_SUBMITTED")
            if state == "COMPLETED":
                return self._public(row, "ALREADY_COMPLETED")
            if state == "TIMED_OUT":
                return self._public(row, "ALREADY_TIMED_OUT")
            if state == "AMBIGUOUS":
                return self._public(row, "AMBIGUOUS")
            if state == "RETRYABLE":
                row = {**row, "state": "SUBMITTING"}
                row.pop("retry_reason", None)
                rows[binding["turn_id"]] = row
                _atomic(self.path, rows)
                return await self._submit_backend(
                    rows=rows,
                    row=row,
                    binding=binding,
                    prompt=prompt,
                    submission_id=submission_id,
                )
            if state != "SUBMITTING":
                raise ValueError("ACTOR_TRANSPORT_STATE_INVALID")

            handle = await self.backend.recover(
                submission_id,
                turn_id=binding["turn_id"],
                actor_kind=binding["actor_kind"],
            )
            if handle is None:
                row = {**row, "state": "AMBIGUOUS"}
                rows[binding["turn_id"]] = row
                _atomic(self.path, rows)
                return self._public(row, "AMBIGUOUS")
            if not isinstance(handle, dict) or handle.get("submission_id") != submission_id:
                raise ValueError("ACTOR_TRANSPORT_RECOVERY_HANDLE_INVALID")
            row = {**row, "state": "SUBMITTED", "handle": copy.deepcopy(handle)}
            rows[binding["turn_id"]] = row
            _atomic(self.path, rows)
            return self._public(row, "RECOVERED")

        row = {
            "protocol_version": "scorp.actor-transport/v3",
            "turn_id": binding["turn_id"],
            "submission_id": submission_id,
            "binding_sha256": binding_sha,
            "binding": binding,
            "state": "SUBMITTING",
        }
        rows[binding["turn_id"]] = row
        _atomic(self.path, rows)
        return await self._submit_backend(
            rows=rows,
            row=row,
            binding=binding,
            prompt=prompt,
            submission_id=submission_id,
        )

    def mark_timed_out(self, turn_id: str, *, reason="GUI_TIMEOUT", timed_out_at: str) -> dict:
        turn_id = _text(turn_id, "ACTOR_TRANSPORT_TURN_ID_MISSING")
        reason = _text(reason, "ACTOR_TRANSPORT_TIMEOUT_REASON_MISSING")
        timed_out_at = _text(timed_out_at, "ACTOR_TRANSPORT_TIMED_OUT_AT_MISSING")
        rows = self._rows()
        row = rows.get(turn_id)
        if not isinstance(row, dict):
            raise ValueError("ACTOR_TRANSPORT_TURN_NOT_FOUND")
        state = row.get("state")
        if state == "TIMED_OUT":
            if row.get("timeout_reason") != reason or row.get("timed_out_at") != timed_out_at:
                raise ValueError("ACTOR_TRANSPORT_TIMEOUT_CONFLICT")
            return self._public(row, "TIMED_OUT")
        if state != "SUBMITTED":
            raise ValueError("ACTOR_TRANSPORT_TIMEOUT_STATE_INVALID")
        row = {
            **row,
            "state": "TIMED_OUT",
            "timeout_reason": reason,
            "timed_out_at": timed_out_at,
        }
        rows[turn_id] = row
        _atomic(self.path, rows)
        return self._public(row, "TIMED_OUT")

    async def poll(self, turn_id: str, *, timeout_seconds=1800) -> dict:
        turn_id = _text(turn_id, "ACTOR_TRANSPORT_TURN_ID_MISSING")
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds <= 0:
            raise ValueError("ACTOR_TRANSPORT_TIMEOUT_INVALID")
        rows = self._rows()
        row = rows.get(turn_id)
        if not isinstance(row, dict):
            raise ValueError("ACTOR_TRANSPORT_TURN_NOT_FOUND")
        state = row.get("state")
        if state == "COMPLETED":
            return self._public(row, "COMPLETED")
        if state == "TIMED_OUT":
            return self._public(row, "TIMED_OUT")
        if state in {"SUBMITTING", "AMBIGUOUS", "RETRYABLE"}:
            return self._public(row, state)
        if state != "SUBMITTED" or not isinstance(row.get("handle"), dict):
            raise ValueError("ACTOR_TRANSPORT_STATE_INVALID")

        result = await self.backend.poll(
            copy.deepcopy(row["handle"]),
            turn_id=row["turn_id"],
            actor_kind=row["binding"]["actor_kind"],
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(result, dict):
            raise ValueError("ACTOR_TRANSPORT_POLL_RESULT_INVALID")
        result_status = str(result.get("status") or "").upper()
        if result_status == "PENDING":
            return self._public(row, "PENDING")
        if result_status != "COMPLETED":
            raise ValueError("ACTOR_TRANSPORT_POLL_STATUS_INVALID")
        response = result.get("response")
        if not isinstance(response, dict):
            raise ValueError("ACTOR_TRANSPORT_RESPONSE_INVALID")
        conversation_url = _text(
            result.get("conversation_url") or row["handle"].get("conversation_url"),
            "ACTOR_TRANSPORT_CONVERSATION_URL_MISSING",
        )
        current_rows = self._rows()
        current = current_rows.get(turn_id)
        if not isinstance(current, dict) or current.get("submission_id") != row["submission_id"] or current.get("state") != "SUBMITTED":
            raise ValueError("ACTOR_TRANSPORT_POLL_STATE_CONFLICT")
        current = {
            **current,
            "state": "COMPLETED",
            "response": copy.deepcopy(response),
            "snapshot": result.get("snapshot"),
            "conversation_url": conversation_url,
        }
        current_rows[turn_id] = current
        _atomic(self.path, current_rows)
        return self._public(current, "COMPLETED")
