"""Explicit candidate entry point joining the GUI bridge to V4 state.

This module is intentionally opt-in. The legacy JSON relay remains available for
compatibility, but it is not allowed to become the V4 authority by accident.
"""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from master_a_dynamic_v4.browser_adapter import BrowserAdapter
from master_a_dynamic_v4.models import CommitResult
from master_a_dynamic_v4.path_policy import PathPolicy
from master_a_dynamic_v4.recovery import recover_pending_intents
from master_a_dynamic_v4.scheduler import Scheduler, SchedulerError
from master_a_dynamic_v4.state_store import StateStore


DEFAULT_QUEUE_REPO = "Scorp96/666"
LEGACY_CONTROL_REPO = "Scorp96/scorp-control-plane"


class QueueConfig:
    def __init__(self, repo: str, *, migration_mode: bool = False):
        value = str(repo or "").strip()
        if not value or "/" not in value or value.count("/") != 1:
            raise ValueError("QUEUE_REPO_INVALID")
        if value == LEGACY_CONTROL_REPO and not migration_mode:
            raise ValueError("LEGACY_CONTROL_REPO_REQUIRES_MIGRATION_MODE")
        self.repo = value
        self.migration_mode = bool(migration_mode)

    @classmethod
    def for_repo(cls, repo: str | None = None, *, migration_mode: bool = False) -> "QueueConfig":
        return cls(repo or DEFAULT_QUEUE_REPO, migration_mode=migration_mode)


class V4BridgeGateway:
    """Small, deterministic seam used by a future bridge runtime.

    The gateway owns the SQLite store and delegates all browser side effects to
    ``BrowserAdapter``. Callers must provide an engine implementing
    ``auth_state``, ``submit`` and ``reconcile``; no implicit login bypass or
    browser discovery is performed here.
    """

    def __init__(
        self,
        database_path: str | pathlib.Path,
        project_id: str,
        allowed_roots: Sequence[str | pathlib.Path],
        engine: Any,
        *,
        protected_roots: Sequence[str | pathlib.Path] = (),
    ):
        self.store = StateStore(database_path, allowed_roots=allowed_roots)
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            self.store.close()
            raise ValueError("PROJECT_ID_EMPTY")
        self.scheduler = Scheduler(
            self.store,
            self.project_id,
            PathPolicy(allowed_roots, protected_roots=protected_roots),
            max_workers=2,
        )
        self.adapter = BrowserAdapter(self.store, engine)

    def close(self) -> None:
        self.store.close()

    def ensure_contract(
        self,
        root_contract: Mapping[str, Any],
        acceptance_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.store.create_contract(
            self.project_id,
            root_contract=root_contract,
            acceptance_contract=acceptance_contract,
        )

    def enqueue_graph(self, tasks: Sequence[Mapping[str, Any]]) -> None:
        self.scheduler.enqueue_graph(tasks)

    def acquire_master_epoch(self, *, expected_epoch: int) -> int:
        """Fence a new logical Master A incarnation in the transaction core."""
        return self.store.advance_master_epoch(
            self.project_id, expected_epoch=int(expected_epoch)
        )

    def commit_master_proposal(
        self,
        transition_id: str,
        proposal: Mapping[str, Any],
        *,
        evidence_refs: Sequence[str] = (),
        expected_version: int | None = None,
        master_epoch: int | None = None,
    ) -> CommitResult:
        """Submit a validated Master A proposal through StateStore's CAS gate.

        Callers should pass the state version and epoch they observed. Omitting
        either uses the current snapshot for convenience, while explicit stale
        values remain fenced and cannot overwrite newer state.
        """
        state = self.store.get_project_state(self.project_id)
        version = int(state["state_version"]) if expected_version is None else int(expected_version)
        epoch = int(state["master_epoch"]) if master_epoch is None else int(master_epoch)
        return self.store.commit(
            version,
            epoch,
            transition_id,
            proposal,
            evidence_refs,
        )

    def claim_workers(self, *, master_epoch: int = 0, limit: int = 2, now=None):
        """Atomically claim up to two runnable dynamic Worker assignments."""
        if int(limit) > 2:
            raise SchedulerError("V4_WORKER_LIMIT_INVALID")
        return self.scheduler.claim_runnable(master_epoch=master_epoch, limit=limit, now=now)

    def record_worker_result(self, claim, *, kind: str, payload: Mapping[str, Any], now=None) -> str:
        return self.scheduler.record_candidate(
            claim.assignment_id,
            lease_token=claim.lease_token,
            master_epoch=claim.master_epoch,
            kind=kind,
            payload=payload,
            now=now,
        )

    def verify_worker_result(self, result_id: str, *, result_sha256: str, now=None) -> None:
        self.scheduler.verify_candidate(result_id, result_sha256=result_sha256, now=now)

    def prepare_browser_intent(
        self,
        intent_id: str,
        prompt: str,
        *,
        actor_id: str = "A",
        channel: str = "master",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = str(prompt or "")
        if not text:
            raise ValueError("PROMPT_EMPTY")
        prompt_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload: dict[str, Any] = {
            "prompt": text,
            "prompt_sha256": prompt_sha256,
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        return self.store.prepare_intent(
            self.project_id,
            intent_id,
            actor_id=actor_id,
            channel=channel,
            action_kind="CHATGPT_SUBMIT",
            payload=payload,
        )

    def submit_intent(self, intent_id: str) -> dict[str, Any]:
        return self.adapter.submit_once(intent_id)

    def recover(self) -> list[tuple[str, str]]:
        return recover_pending_intents(self.adapter)

    def describe(self) -> dict[str, Any]:
        state = self.store.get_project_state(self.project_id)
        return {
            "project_id": self.project_id,
            "master_epoch": int(state["master_epoch"]),
            "state_version": int(state["state_version"]),
            "status": str(state["status"]),
            "pending_intents": len(self.store.pending_intents()),
            "queue_authority": "sqlite",
            "legacy_json_role": "import_or_read_only_compatibility",
        }
