from __future__ import annotations

import datetime as dt

from gui_transport import render_actor_prompt_v3
from master_worker_relay_v3 import (
    GUI_TIMEOUT_SECONDS,
    MasterWorkerRelayV3,
    _atomic,
    _load,
    _sha,
    _validate_turn,
)


class ParallelMasterWorkerRelayV3(MasterWorkerRelayV3):
    """Completion-first nonblocking relay with bounded remote actor concurrency.

    Desktop input remains serialized by the transport driver, but each submit
    returns once a canonical conversation URL exists. Remote ChatGPT generations
    may therefore overlap while this relay polls and routes them independently.
    """

    def __init__(
        self,
        root,
        sessions,
        transport,
        master_transition=None,
        turn_scheduler=None,
        *,
        max_inflight=4,
        max_workers=3,
        chat_resources=None,
        worker_pool=None,
        adaptive_browser_poll=False,
    ):
        super().__init__(
            root,
            sessions,
            gui_turn=None,
            master_transition=master_transition,
            turn_scheduler=turn_scheduler,
        )
        self.transport = transport
        self.chat_resources = chat_resources
        self.worker_pool = worker_pool
        self.adaptive_browser_poll = bool(adaptive_browser_poll)
        self.max_inflight = int(max_inflight)
        if self.max_inflight < 1 or self.max_inflight > 9:
            raise ValueError("PARALLEL_RELAY_INFLIGHT_LIMIT_INVALID")
        self.max_workers = int(max_workers)
        if self.max_workers < 1 or self.max_workers > 8:
            raise ValueError("PARALLEL_RELAY_WORKER_LIMIT_INVALID")

    @staticmethod
    def _utc_text(value=None):
        value = value or dt.datetime.now(dt.timezone.utc)
        if value.tzinfo is None:
            raise ValueError("PARALLEL_RELAY_NOW_MUST_BE_AWARE")
        return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _worker_resource_scope(turn):
        if str((turn or {}).get("actor_kind") or "").upper() != "WORKER":
            return frozenset()
        payload = (turn or {}).get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("WORKER_TURN_PAYLOAD_INVALID")
        assignment = payload.get("assignment") or {}
        if not isinstance(assignment, dict):
            raise ValueError("WORKER_ASSIGNMENT_INVALID")
        raw = assignment.get("resource_scope") or []
        if not isinstance(raw, list):
            raise ValueError("WORKER_RESOURCE_SCOPE_INVALID")
        scope = set()
        for value in raw:
            text = str(value or "").strip()
            if not text:
                raise ValueError("WORKER_RESOURCE_SCOPE_INVALID")
            scope.add(text)
        return frozenset(scope)

    def _turn_for_row(self, turn_id, row):
        path = self.outbox / f"{turn_id}.json"
        turn = _load(path)
        if not isinstance(turn, dict):
            raise ValueError("PARALLEL_RELAY_TURN_MISSING")
        _validate_turn(turn)
        digest = _sha(turn)
        if row.get("envelope_sha256") and row.get("envelope_sha256") != digest:
            raise ValueError("ACTOR_TURN_CONFLICT")
        return digest, turn

    def _candidate_rows(self, ledger):
        found = []
        for path in sorted(self.outbox.glob("*.json")):
            turn = _load(path)
            _validate_turn(turn)
            turn_id = turn["turn_id"]
            digest = _sha(turn)
            old = ledger.get(turn_id) or {}
            if old.get("envelope_sha256") and old.get("envelope_sha256") != digest:
                raise ValueError("ACTOR_TURN_CONFLICT")
            if old.get("state") == "ROUTED":
                payload = turn.get("payload") or {}
                event_id = str(payload.get("worker_event_id") or "").strip() if isinstance(payload, dict) else ""
                journaled_routed = self.response_journal.load(turn_id, digest)
                journaled_response = (journaled_routed or {}).get("response") or {}
                if (
                    turn["actor_kind"].upper() == "MASTER"
                    and event_id
                    and not old.get("master_transition_id")
                    and isinstance(journaled_response, dict)
                    and str(journaled_response.get("kind") or "").upper() == "WAIT"
                ):
                    event = self.worker_events.get(event_id)
                    if event.get("state") == "PENDING":
                        found.append({
                            "turn_id": turn_id,
                            "digest": digest,
                            "turn": turn,
                            "replayable": True,
                        })
                if (
                    turn["actor_kind"].upper() == "MASTER"
                    and event_id
                    and old.get("master_transition_id")
                    and not list(old.get("children") or [])
                    and isinstance(journaled_response, dict)
                    and str(journaled_response.get("kind") or "").upper() == "WAIT"
                ):
                    event = self.worker_events.get(event_id)
                    try:
                        routed_state_version = int(old.get("project_state_version"))
                    except Exception:
                        routed_state_version = -1
                    if (
                        event.get("state") == "ACKED"
                        and event.get("ack_transition_id") == old.get("master_transition_id")
                        and routed_state_version == int(turn["state_version"]) + 1
                    ):
                        found.append({
                            "turn_id": turn_id,
                            "digest": digest,
                            "turn": turn,
                            "replayable": True,
                        })
            if old.get("state") in {"ROUTED", "SUPERSEDED", "GUI_SUBMITTED", "GUI_AMBIGUOUS", "GUI_TIMED_OUT"}:
                continue
            journaled = self.response_journal.load(turn_id, digest)
            found.append({
                "turn_id": turn_id,
                "digest": digest,
                "turn": turn,
                "replayable": journaled is not None,
            })
        return found

    def _apply_superseded(self, ledger, found, superseded):
        if not superseded:
            return
        by_id = {row["turn_id"]: row for row in found}
        stamp = self._utc_text()
        changed = False
        for row in superseded:
            turn_id = row["turn_id"]
            candidate = by_id.get(turn_id)
            if candidate is None:
                continue
            turn = candidate["turn"]
            ledger[turn_id] = {
                **(ledger.get(turn_id) or {}),
                "state": "SUPERSEDED",
                "envelope_sha256": candidate["digest"],
                "actor_kind": turn["actor_kind"].upper(),
                "actor_id": turn["actor_id"],
                "session_id": turn["session_id"],
                "superseded_reason": row["reason"],
                "superseded_at": stamp,
            }
            changed = True
        if changed:
            self._save_ledger(ledger)

    def _select_candidate(
        self,
        found,
        ledger,
        *,
        now,
        master_inflight,
        worker_inflight_count=0,
        worker_scopes=(),
    ):
        pool = list(found)
        while pool:
            if self.turn_scheduler is None:
                pool.sort(key=lambda row: row["turn_id"])
                selected = pool[0]
                superseded = []
            else:
                scheduled = self.turn_scheduler.select(pool, ledger, now=now)
                superseded = scheduled.get("superseded") or []
                self._apply_superseded(ledger, pool, superseded)
                superseded_ids = {row["turn_id"] for row in superseded}
                if superseded_ids:
                    pool = [row for row in pool if row["turn_id"] not in superseded_ids]
                selected = scheduled.get("selected")
            if selected is None:
                return None
            actor_kind = str(selected["turn"]["actor_kind"]).upper()
            if master_inflight and actor_kind == "MASTER":
                pool = [row for row in pool if row["turn_id"] != selected["turn_id"]]
                continue
            if actor_kind == "WORKER":
                if worker_inflight_count >= self.max_workers:
                    pool = [row for row in pool if row["turn_id"] != selected["turn_id"]]
                    continue
                candidate_scope = self._worker_resource_scope(selected["turn"])
                if candidate_scope and any(candidate_scope.intersection(scope) for scope in worker_scopes):
                    pool = [row for row in pool if row["turn_id"] != selected["turn_id"]]
                    continue
            return selected
        return None

    def _inflight_rows(self, ledger):
        rows = []
        for turn_id, row in ledger.items():
            if isinstance(row, dict) and row.get("state") == "GUI_SUBMITTED":
                rows.append((str(turn_id), row))
        rows.sort(key=lambda item: item[0])
        return rows

    def _inflight_worker_state(self, ledger):
        count = 0
        scopes = []
        for turn_id, row in self._inflight_rows(ledger):
            if str(row.get("actor_kind") or "").upper() != "WORKER":
                continue
            count += 1
            _, turn = self._turn_for_row(turn_id, row)
            scope = self._worker_resource_scope(turn)
            if scope:
                scopes.append(scope)
        return count, tuple(scopes)

    async def _finish_response(self, turn, digest, result, *, now=None):
        turn_id = turn["turn_id"]
        response = result.get("response")
        if not isinstance(response, dict):
            raise ValueError("ACTOR_RESPONSE_NOT_OBJECT")
        conversation_url = str(result.get("conversation_url") or "").strip()
        if not conversation_url:
            raise ValueError("ACTOR_CONVERSATION_URL_MISSING")

        ledger = self._ledger()
        old = ledger.get(turn_id) or {}
        submitted_url = str(old.get("conversation_url") or "").strip()
        if submitted_url and submitted_url != conversation_url:
            raise ValueError("ACTOR_CONVERSATION_CHANGED")

        actor_kind = turn["actor_kind"].upper()
        pooled_worker = actor_kind == "WORKER" and self.worker_pool is not None
        if pooled_worker:
            lease = self.worker_pool.resolve(
                turn["project_id"], turn["actor_id"], turn["session_id"], turn_id
            )
            if lease is None:
                lease = self.worker_pool.acquire(
                    turn["project_id"], turn["actor_id"], turn["session_id"], turn_id
                )
                if lease is None:
                    raise ValueError("WORKER_SLOT_LEASE_MISSING")
            bound_url = str(lease.get("conversation_url") or "").strip()
            if not bound_url:
                lease = self.worker_pool.bind_conversation(
                    turn["project_id"], turn["actor_id"], turn["session_id"], turn_id, conversation_url
                )
                bound_url = str(lease.get("conversation_url") or "").strip()
            if bound_url != conversation_url:
                raise ValueError("WORKER_SLOT_CONVERSATION_CHANGED")
            canonical_url = conversation_url
        else:
            canonical_url = self.sessions.record_session(
                turn["project_id"],
                turn["session_id"],
                actor_kind,
                turn["actor_id"],
                turn_id,
                conversation_url,
            )
        self.response_journal.record(turn_id, digest, response)
        captured = {
            "protocol_version": "scorp.master-worker/response-v3",
            "turn": turn,
            "response": response,
            "conversation_url": canonical_url,
            "captured_at": self._utc_text(),
        }
        _atomic(self.inbox / f"{turn_id}.json", captured)

        transition_result = None
        committed_state_version = None
        if turn["actor_kind"].upper() == "MASTER":
            transition_result = self._commit_master_response(turn, response, now=now)
            if transition_result is not None:
                committed_state_version = transition_result["state"]["state_version"]

        if committed_state_version is None:
            children = self._route(turn, response)
        else:
            children = self._route(turn, response, committed_state_version=committed_state_version)

        ledger = self._ledger()
        current = ledger.get(turn_id) or {}
        if current.get("envelope_sha256") and current["envelope_sha256"] != digest:
            raise ValueError("ACTOR_TURN_CONFLICT")
        row = {
            **current,
            "state": "ROUTED",
            "envelope_sha256": digest,
            "actor_kind": turn["actor_kind"].upper(),
            "actor_id": turn["actor_id"],
            "session_id": turn["session_id"],
            "response_sha256": _sha(response),
            "conversation_url": canonical_url,
            "children": children,
            "routed_at": self._utc_text(),
        }
        if transition_result is not None:
            row["master_transition_id"] = transition_result["transition_id"]
            row["project_state_version"] = transition_result["state"]["state_version"]
            row["master_transition_replayed"] = bool(transition_result.get("replayed"))
        ledger[turn_id] = row
        self._save_ledger(ledger)
        if pooled_worker:
            self.worker_pool.release(
                turn["project_id"], turn["actor_id"], turn["session_id"], turn_id
            )
        return children

    @staticmethod
    def _parse_utc_text(value):
        text = str(value or "").strip()
        if not text:
            raise ValueError("PARALLEL_RELAY_SUBMITTED_AT_MISSING")
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("PARALLEL_RELAY_SUBMITTED_AT_INVALID") from exc
        if parsed.tzinfo is None:
            raise ValueError("PARALLEL_RELAY_SUBMITTED_AT_INVALID")
        return parsed.astimezone(dt.timezone.utc)

    @staticmethod
    def _browser_poll_interval_seconds(elapsed_seconds):
        elapsed = max(0.0, float(elapsed_seconds))
        if elapsed < 300.0:
            return 60
        if elapsed < 900.0:
            return 120
        return 300

    def _browser_poll_due(self, row, current_now):
        submitted_at = self._parse_utc_text(row.get("submitted_at"))
        timeout_at = submitted_at + dt.timedelta(seconds=GUI_TIMEOUT_SECONDS)
        if current_now >= timeout_at:
            return True
        next_poll_text = str(row.get("next_poll_at") or "").strip()
        if next_poll_text:
            next_poll_at = self._parse_utc_text(next_poll_text)
        else:
            last_poll_text = str(row.get("last_polled_at") or "").strip()
            if last_poll_text:
                last_polled_at = self._parse_utc_text(last_poll_text)
                elapsed = (last_polled_at - submitted_at).total_seconds()
                interval = self._browser_poll_interval_seconds(elapsed)
                next_poll_at = last_polled_at + dt.timedelta(seconds=interval)
            else:
                next_poll_at = submitted_at + dt.timedelta(seconds=60)
            if next_poll_at > timeout_at:
                next_poll_at = timeout_at
        return current_now >= next_poll_at

    def _record_browser_poll(self, turn_id, current_now):
        fresh = self._ledger()
        current = fresh.get(turn_id) or {}
        submitted_at = self._parse_utc_text(current.get("submitted_at"))
        elapsed = (current_now - submitted_at).total_seconds()
        interval = self._browser_poll_interval_seconds(elapsed)
        timeout_at = submitted_at + dt.timedelta(seconds=GUI_TIMEOUT_SECONDS)
        next_poll_at = current_now + dt.timedelta(seconds=interval)
        if next_poll_at > timeout_at:
            next_poll_at = timeout_at
        try:
            poll_count = int(current.get("browser_poll_count") or 0) + 1
        except Exception as exc:
            raise ValueError("PARALLEL_RELAY_BROWSER_POLL_COUNT_INVALID") from exc
        fresh[turn_id] = {
            **current,
            "last_polled_at": self._utc_text(current_now),
            "next_poll_at": self._utc_text(next_poll_at),
            "browser_poll_count": poll_count,
        }
        self._save_ledger(fresh)

    async def _terminalize_timeout(self, turn_id, row, digest, turn, *, now, reason, timed_out_at):
        conversation_url = str(row.get("conversation_url") or "").strip()
        if not conversation_url:
            raise ValueError("ACTOR_CONVERSATION_URL_MISSING")
        actor_kind = str(turn["actor_kind"]).upper()
        if actor_kind == "MASTER":
            self.sessions.record_session(
                turn["project_id"],
                turn["session_id"],
                actor_kind,
                turn["actor_id"],
                turn_id,
                conversation_url,
            )
            fresh = self._ledger()
            current = fresh.get(turn_id) or {}
            if current.get("envelope_sha256") and current["envelope_sha256"] != digest:
                raise ValueError("ACTOR_TURN_CONFLICT")
            fresh[turn_id] = {
                **current,
                "state": "GUI_TIMED_OUT",
                "timeout_reason": reason,
                "timed_out_at": timed_out_at,
            }
            self._save_ledger(fresh)
            return False

        if actor_kind != "WORKER":
            raise ValueError("ACTOR_KIND_INVALID")
        fresh = self._ledger()
        current = fresh.get(turn_id) or {}
        if current.get("envelope_sha256") and current["envelope_sha256"] != digest:
            raise ValueError("ACTOR_TURN_CONFLICT")
        fresh[turn_id] = {
            **current,
            "timeout_reason": reason,
            "timed_out_at": timed_out_at,
        }
        self._save_ledger(fresh)
        await self._finish_response(
            turn,
            digest,
            {
                "status": "COMPLETED",
                "response": {
                    "kind": "BLOCKER",
                    "reason": "ACTOR_GUI_TIMEOUT",
                    "source_turn_id": turn_id,
                },
                "snapshot": None,
                "conversation_url": conversation_url,
            },
            now=now,
        )
        return True

    async def _collect_completions(self, *, now=None):
        completed = 0
        ambiguous = 0
        timed_out = 0
        current_now = now or dt.datetime.now(dt.timezone.utc)
        if current_now.tzinfo is None:
            raise ValueError("PARALLEL_RELAY_NOW_MUST_BE_AWARE")
        current_now = current_now.astimezone(dt.timezone.utc)
        ledger = self._ledger()
        for turn_id, row in self._inflight_rows(ledger):
            digest, turn = self._turn_for_row(turn_id, row)
            if self.adaptive_browser_poll and not self._browser_poll_due(row, current_now):
                continue
            result = await self.transport.poll(turn_id, timeout_seconds=GUI_TIMEOUT_SECONDS)
            if self.adaptive_browser_poll:
                self._record_browser_poll(turn_id, current_now)
            if not isinstance(result, dict):
                raise ValueError("PARALLEL_RELAY_POLL_RESULT_INVALID")
            status = str(result.get("status") or "").upper()
            if status in {"PENDING", "SUBMITTING"}:
                submitted_at = self._parse_utc_text(row.get("submitted_at"))
                if (current_now - submitted_at).total_seconds() < GUI_TIMEOUT_SECONDS:
                    continue
                timed_out_at = self._utc_text(current_now)
                reason = "GUI_TIMEOUT"
                self.transport.mark_timed_out(
                    turn_id,
                    reason=reason,
                    timed_out_at=timed_out_at,
                )
                worker_completed = await self._terminalize_timeout(
                    turn_id,
                    row,
                    digest,
                    turn,
                    now=current_now,
                    reason=reason,
                    timed_out_at=timed_out_at,
                )
                timed_out += 1
                if worker_completed:
                    completed += 1
                ledger = self._ledger()
                continue
            if status == "TIMED_OUT":
                timed_out_at = str(result.get("timed_out_at") or self._utc_text(current_now))
                reason = str(result.get("reason") or "GUI_TIMEOUT")
                worker_completed = await self._terminalize_timeout(
                    turn_id,
                    row,
                    digest,
                    turn,
                    now=current_now,
                    reason=reason,
                    timed_out_at=timed_out_at,
                )
                timed_out += 1
                if worker_completed:
                    completed += 1
                ledger = self._ledger()
                continue
            if status == "AMBIGUOUS":
                fresh = self._ledger()
                fresh[turn_id] = {**(fresh.get(turn_id) or {}), "state": "GUI_AMBIGUOUS"}
                self._save_ledger(fresh)
                ambiguous += 1
                continue
            if status != "COMPLETED":
                raise ValueError("PARALLEL_RELAY_POLL_STATUS_INVALID")
            await self._finish_response(turn, digest, result, now=current_now)
            completed += 1
            ledger = self._ledger()
        return completed, ambiguous, timed_out

    async def _recover_journaled(self, candidate, *, now=None):
        turn = candidate["turn"]
        turn_id = candidate["turn_id"]
        journaled = self.response_journal.load(turn_id, candidate["digest"])
        if journaled is None:
            return False
        url = self.sessions.resolve_actor_conversation(turn["project_id"], turn["session_id"], turn["actor_kind"].upper(), turn["actor_id"])
        if not url:
            row = self._ledger().get(turn_id) or {}
            url = row.get("conversation_url")
        if not url:
            raise ValueError("ACTOR_RESPONSE_SESSION_MISSING")
        await self._finish_response(
            turn,
            candidate["digest"],
            {
                "status": "COMPLETED",
                "response": journaled["response"],
                "snapshot": None,
                "conversation_url": url,
            },
            now=now,
        )
        return True

    async def _submit_candidate(self, candidate, *, now=None):
        turn = candidate["turn"]
        turn_id = candidate["turn_id"]
        actor_kind = turn["actor_kind"].upper()
        worker_lease = None
        if actor_kind == "WORKER" and self.worker_pool is not None:
            worker_lease = self.worker_pool.acquire(
                turn["project_id"], turn["actor_id"], turn["session_id"], turn_id
            )
            if worker_lease is None:
                return "DEFERRED"
            existing_url = worker_lease.get("conversation_url")
        else:
            existing_url = self.sessions.resolve_actor_conversation(
                turn["project_id"], turn["session_id"], actor_kind, turn["actor_id"]
            )
        needs_new_chat = not bool(str(existing_url or "").strip())
        if self.chat_resources is not None:
            permission = self.chat_resources.permission(now=now, needs_new_chat=needs_new_chat)
            if not bool(permission.get("allowed")):
                return "DEFERRED"
            if needs_new_chat:
                self.chat_resources.reserve_new_chat(turn_id, now=now)
        result = await self.transport.submit(
            prompt=render_actor_prompt_v3(turn),
            turn_id=turn_id,
            actor_kind=turn["actor_kind"].upper(),
            conversation_url=existing_url,
            timeout_seconds=GUI_TIMEOUT_SECONDS,
        )
        if not isinstance(result, dict):
            raise ValueError("PARALLEL_RELAY_SUBMIT_RESULT_INVALID")
        status = str(result.get("status") or "").upper()
        ledger = self._ledger()
        old = ledger.get(turn_id) or {}
        if old.get("envelope_sha256") and old["envelope_sha256"] != candidate["digest"]:
            raise ValueError("ACTOR_TURN_CONFLICT")
        if status == "AMBIGUOUS":
            ledger[turn_id] = {
                **old,
                "state": "GUI_AMBIGUOUS",
                "envelope_sha256": candidate["digest"],
                "actor_kind": turn["actor_kind"].upper(),
                "actor_id": turn["actor_id"],
                "session_id": turn["session_id"],
            }
            self._save_ledger(ledger)
            return "AMBIGUOUS"
        if status == "DEFERRED":
            reason = str(result.get("reason") or "").strip()
            if not reason:
                raise ValueError("PARALLEL_RELAY_DEFERRED_REASON_MISSING")
            if self.chat_resources is not None:
                self.chat_resources.note_throttle(reason, now=now)
            return "DEFERRED"
        if status not in {"SUBMITTED", "ALREADY_SUBMITTED", "RECOVERED", "ALREADY_COMPLETED"}:
            raise ValueError("PARALLEL_RELAY_SUBMIT_STATUS_INVALID")
        url = result.get("conversation_url")
        if not url and isinstance(result.get("handle"), dict):
            url = result["handle"].get("conversation_url")
        if not str(url or "").strip():
            raise ValueError("ACTOR_CONVERSATION_URL_MISSING")
        if actor_kind == "WORKER" and self.worker_pool is not None:
            self.worker_pool.bind_conversation(
                turn["project_id"], turn["actor_id"], turn["session_id"], turn_id, url
            )
        ledger[turn_id] = {
            **old,
            "state": "GUI_SUBMITTED",
            "envelope_sha256": candidate["digest"],
            "actor_kind": turn["actor_kind"].upper(),
            "actor_id": turn["actor_id"],
            "session_id": turn["session_id"],
            "submission_id": result.get("submission_id"),
            "conversation_url": url,
            "submitted_at": self._utc_text(now),
        }
        self._save_ledger(ledger)
        return "SUBMITTED"

    def _reconcile_worker_pool_leases(self, ledger):
        if self.worker_pool is None:
            return 0
        released = 0
        for lease in self.worker_pool.active_leases():
            turn_id = str(lease.get("turn_id") or "").strip()
            if not turn_id:
                raise ValueError("WORKER_SLOT_TURN_ID_MISSING")
            row = ledger.get(turn_id) or {}
            if not isinstance(row, dict):
                raise ValueError("PARALLEL_RELAY_LEDGER_ROW_INVALID")
            if row.get("state") != "ROUTED":
                continue
            if str(row.get("actor_kind") or "").upper() != "WORKER":
                raise ValueError("WORKER_SLOT_ROUTED_ACTOR_INVALID")
            self.worker_pool.release(
                lease["project_id"], lease["actor_id"], lease["session_id"], lease["turn_id"]
            )
            released += 1
        return released

    async def run_tick(self, *, now=None):
        reconciled_worker_leases = self._reconcile_worker_pool_leases(self._ledger())
        completed, ambiguous, timed_out = await self._collect_completions(now=now)
        submitted = 0
        recovered = 0
        deferred = 0
        deferred_turns = set()

        while True:
            ledger = self._ledger()
            inflight_rows = self._inflight_rows(ledger)
            if len(inflight_rows) >= self.max_inflight:
                break
            master_inflight = any(str(row.get("actor_kind") or "").upper() == "MASTER" for _, row in inflight_rows)
            worker_inflight_count, worker_scopes = self._inflight_worker_state(ledger)
            found = [row for row in self._candidate_rows(ledger) if row["turn_id"] not in deferred_turns]
            if not found:
                break
            candidate = self._select_candidate(
                found,
                ledger,
                now=now,
                master_inflight=master_inflight,
                worker_inflight_count=worker_inflight_count,
                worker_scopes=worker_scopes,
            )
            if candidate is None:
                break
            if candidate.get("replayable"):
                if await self._recover_journaled(candidate, now=now):
                    recovered += 1
                    completed += 1
                    break
            outcome = await self._submit_candidate(candidate, now=now)
            if outcome == "DEFERRED":
                deferred += 1
                deferred_turns.add(candidate["turn_id"])
            elif outcome == "AMBIGUOUS":
                ambiguous += 1
            else:
                submitted += 1

        inflight = len(self._inflight_rows(self._ledger()))
        return {
            "status": "ACTIVE" if inflight or submitted or completed or timed_out else "IDLE",
            "submitted": submitted,
            "deferred": deferred,
            "completed": completed,
            "recovered": recovered,
            "ambiguous": ambiguous,
            "timed_out": timed_out,
            "reconciled_worker_leases": reconciled_worker_leases,
            "inflight": inflight,
        }
