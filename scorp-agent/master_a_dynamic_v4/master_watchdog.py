from __future__ import annotations

import datetime as dt

from .state_store import StateStore


class MasterWatchdog:
    """SQLite-backed liveness gate for the single logical Master A.

    The watchdog never opens a browser or starts a model session. It records
    the lease and returns a durable, idempotent resume signal for the adapter
    that owns the physical ChatGPT session.
    """

    def __init__(self, store: StateStore, project_id: str, *, ttl_seconds: int = 1500):
        self.store = store
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("PROJECT_ID_EMPTY")
        self.ttl_seconds = int(ttl_seconds)
        if self.ttl_seconds <= 0:
            raise ValueError("MASTER_SESSION_TTL_INVALID")

    def start(self, session_id: str, *, now: dt.datetime | None = None) -> dict:
        return self.store.start_master_session(
            self.project_id,
            session_id,
            now=now,
            ttl_seconds=self.ttl_seconds,
        )

    def heartbeat(
        self,
        session_id: str,
        *,
        master_epoch: int,
        now: dt.datetime | None = None,
    ) -> dict:
        return self.store.heartbeat_master_session(
            self.project_id,
            session_id,
            master_epoch=master_epoch,
            now=now,
            ttl_seconds=self.ttl_seconds,
        )

    def run_once(self, *, now: dt.datetime | None = None) -> dict:
        return self.store.inspect_master_session(self.project_id, now=now)

    def end(
        self,
        session_id: str,
        *,
        master_epoch: int,
        reason: str,
        now: dt.datetime | None = None,
    ) -> dict:
        return self.store.end_master_session(
            self.project_id,
            session_id,
            master_epoch=master_epoch,
            reason=reason,
            now=now,
        )
