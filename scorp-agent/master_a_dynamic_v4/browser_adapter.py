from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Mapping
from typing import Any

from .models import IntentState
from .state_store import StateStore, StoreInvariantError


class BrowserAdapterError(RuntimeError):
    pass


class BrowserBindingError(BrowserAdapterError):
    pass


class ReconcileRequired(BrowserAdapterError):
    pass


class InjectedCrash(BrowserAdapterError):
    pass


class BrowserAdapter:
    def __init__(self, store: StateStore, engine: Any, *, failpoint: str | None = None):
        self.store = store
        self.engine = engine
        self.failpoint = failpoint

    def _crash(self, point: str) -> None:
        if self.failpoint == point:
            raise InjectedCrash(point)

    @staticmethod
    def _engine_intent(row: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = json.loads(value["payload_json"])
        return value

    def submit_once(self, intent_id: str) -> dict[str, Any]:
        current = self.store.get_intent(intent_id)
        if current["state"] == IntentState.RESPONSE_CAPTURED.value:
            return current
        if current["state"] == IntentState.CONFIRMED_SUBMITTED.value:
            return current
        if current["state"] == IntentState.MAY_HAVE_SUBMITTED.value:
            raise ReconcileRequired(intent_id)
        if current["state"] == IntentState.BLOCKED_AMBIGUOUS.value:
            raise ReconcileRequired(intent_id)
        if current["state"] not in {
            IntentState.PREPARED.value,
            IntentState.VERIFIED_NOT_SUBMITTED.value,
        }:
            raise BrowserAdapterError(f"INTENT_STATE_INVALID: {current['state']}")

        try:
            self.store.assert_intent_generation(intent_id)
        except StoreInvariantError as exc:
            return self.store.block_intent(
                intent_id,
                reason=f"OPERATOR_GENERATION_FENCED:{exc}",
                observation={"side_effect": "NOT_ATTEMPTED", "reason": str(exc)},
            )

        auth = self.engine.auth_state(current["channel"])
        auth_status = str((auth or {}).get("status") or "UNKNOWN_AUTH_STATE")
        if auth_status != "AUTHENTICATED":
            return self.store.block_intent(
                intent_id,
                reason=f"AUTH_BLOCKED:{auth_status}",
                observation=dict(auth or {"status": "UNKNOWN_AUTH_STATE"}),
            )

        try:
            persisted = self.store.begin_possible_submit(intent_id)
        except StoreInvariantError as exc:
            return self.store.block_intent(
                intent_id,
                reason=f"OPERATOR_GENERATION_FENCED:{exc}",
                observation={"side_effect": "NOT_ATTEMPTED", "reason": str(exc)},
            )
        except sqlite3.Error as exc:
            # A failed SQLite durability fence is not an ambiguous browser
            # result: the external call has not started and must never be
            # attempted.  Do not call block_intent here because the same
            # database failure may make a second write unsafe.
            raise BrowserAdapterError("SQLITE_WRITE_FAILED") from exc
        try:
            # Recheck after the durable MAY_HAVE_SUBMITTED fence and directly
            # before the external call. A pause/cancel committed in between
            # must fail closed instead of sending a stale prompt.
            self.store.assert_intent_generation(intent_id)
        except StoreInvariantError as exc:
            return self.store.block_intent(
                intent_id,
                reason=f"OPERATOR_GENERATION_FENCED:{exc}",
                observation={"side_effect": "NOT_ATTEMPTED", "reason": str(exc)},
            )
        self._crash("after_may_have_submitted")
        try:
            observation = self.engine.submit(self._engine_intent(persisted))
        except InjectedCrash:
            raise
        except Exception as exc:
            # Once MAY_HAVE_SUBMITTED is durable, a transport exception is
            # ambiguous even when the client reports an EOF before returning a
            # response. Preserve the side-effect fence and make the blocker
            # explicit; callers must reconcile read-only before any retry.
            message = str(exc)
            return self.store.block_intent(
                intent_id,
                reason="SUBMIT_EXCEPTION_AMBIGUOUS",
                observation={
                    "error_type": type(exc).__name__,
                    "error_message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
                },
            )
        self._crash("after_remote_submit")
        if not isinstance(observation, Mapping):
            return self.store.block_intent(
                intent_id, reason="SUBMIT_RESULT_NOT_MAPPING", observation={"type": type(observation).__name__}
            )
        status = str(observation.get("status") or "").upper()
        url = str(observation.get("conversation_url") or "").strip()
        remote = str(observation.get("remote_identity") or "").strip()
        if status == "RESPONSE_CAPTURED":
            response = observation.get("response")
            if not url or not remote or not isinstance(response, Mapping) or not response:
                return self.store.block_intent(
                    intent_id,
                    reason="RESPONSE_CAPTURE_IDENTITY_AMBIGUOUS",
                    observation=dict(observation),
                )
            captured = self.store.capture_response(
                intent_id,
                response=dict(response),
                conversation_url=url,
                remote_identity=remote,
                observation=dict(observation),
            )
            self.store.finalize_intent(intent_id)
            return captured
        if status != "SUBMITTED" or not url or not remote:
            return self.store.block_intent(
                intent_id,
                reason="SUBMIT_IDENTITY_AMBIGUOUS",
                observation=dict(observation),
            )
        return self.store.confirm_submitted(
            intent_id,
            conversation_url=url,
            remote_identity=remote,
            observation=dict(observation),
        )

    def reconcile(self, intent_id: str) -> dict[str, Any]:
        current = self.store.get_intent(intent_id)
        if current["state"] == IntentState.RESPONSE_CAPTURED.value:
            self.store.finalize_intent(intent_id)
            return self.store.get_intent(intent_id)
        observation = self.engine.reconcile(self._engine_intent(current))
        if not isinstance(observation, Mapping):
            return self.store.block_intent(
                intent_id,
                reason="RECONCILE_RESULT_NOT_MAPPING",
                observation={"type": type(observation).__name__},
            )
        status = str(observation.get("status") or "").upper()
        if status == "VERIFIED_NOT_SUBMITTED":
            proof = str(observation.get("proof") or "").strip()
            if not proof:
                return self.store.block_intent(
                    intent_id,
                    reason="NOT_SUBMITTED_WITHOUT_POSITIVE_PROOF",
                    observation=dict(observation),
                )
            return self.store.mark_verified_not_submitted(
                intent_id, proof=proof, observation=dict(observation)
            )
        if status == "CONFIRMED_SUBMITTED":
            url = str(observation.get("conversation_url") or "").strip()
            remote = str(observation.get("remote_identity") or "").strip()
            if not url or not remote:
                return self.store.block_intent(
                    intent_id,
                    reason="RECONCILE_SUBMIT_IDENTITY_AMBIGUOUS",
                    observation=dict(observation),
                )
            return self.store.confirm_submitted(
                intent_id,
                conversation_url=url,
                remote_identity=remote,
                observation=dict(observation),
            )
        if status == "RESPONSE_CAPTURED":
            url = str(observation.get("conversation_url") or current.get("conversation_url") or "").strip()
            remote = str(observation.get("remote_identity") or current.get("remote_identity") or "").strip()
            response = observation.get("response")
            if not url or not remote or not isinstance(response, Mapping) or not response:
                return self.store.block_intent(
                    intent_id,
                    reason="RESPONSE_CAPTURE_AMBIGUOUS",
                    observation=dict(observation),
                )
            captured = self.store.capture_response(
                intent_id,
                response=dict(response),
                conversation_url=url,
                remote_identity=remote,
                observation=dict(observation),
            )
            self._crash("after_response_capture")
            self.store.finalize_intent(intent_id)
            return captured
        return self.store.block_intent(
            intent_id,
            reason=str(observation.get("reason") or "RECONCILE_AMBIGUOUS"),
            observation=dict(observation),
        )

    def rebind(
        self,
        project_id: str,
        channel: str,
        *,
        actor_id: str,
        conversation_url: str,
        predecessor_url: str | None,
        reason: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.store.rebind_browser(
                project_id,
                channel,
                actor_id=actor_id,
                conversation_url=conversation_url,
                predecessor_url=predecessor_url,
                reason=reason,
                evidence=evidence,
            )
        except StoreInvariantError as exc:
            raise BrowserBindingError(str(exc)) from exc
