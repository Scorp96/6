from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import dataclasses
import hashlib
import hmac
import json
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.continuation_gate import ContinuationRequest
from master_a_dynamic_v4.host_terminal_receipt import HostTerminalReceipt
from master_a_dynamic_v4.isolated_verified_continuation_latch import (
    reserve_verified_continuation_for_review,
)
from master_a_dynamic_v4.session_admission import AdmissionPolicy, SessionObservation
from master_a_dynamic_v4.turn_completion_evidence import TurnSample

KEY = b"ISOLATED-UNIT-TEST-KEY-not-a-real-host-secret-20261009"
URL = "https://chatgpt.com/c/isolated-atomic-worker"
RESPONSE = "a" * 64


def sealed(receipt: HostTerminalReceipt) -> HostTerminalReceipt:
    fields = dataclasses.asdict(receipt)
    fields.pop("signature")
    proof = hmac.new(
        KEY, json.dumps(fields, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return replace(receipt, signature=proof)


def evidence(*, url=URL, session="isolated-worker-1", intent="safe-intent-1",
             event="e"*32, first_seq=1):
    samples, receipts = [], []
    for ms, seq in ((1000, first_seq), (5000, first_seq+1)):
        sample = TurnSample(
            session_id=session, conversation_url=url, binding_generation=7,
            intent_id=intent, sampled_at_ms=ms, generating=False,
            tool_pending=False, response_sha256=RESPONSE,
            response_intent_verified=True, finish_event="TURN_FINAL_CONFIRMED",
            finish_event_provenance="HOST_VERIFIED",
        )
        receipt = HostTerminalReceipt(
            protocol="scorp.browser-terminal-receipt/1", event_id=event,
            sequence=seq, session_id=session, conversation_url=url,
            binding_generation=7, intent_id=intent, sampled_at_ms=ms,
            response_sha256=RESPONSE, generating=False, tool_pending=False,
            response_intent_verified=True, terminal_event="TURN_FINAL_CONFIRMED",
        )
        samples.append(sample)
        receipts.append(sealed(receipt))
    return samples, receipts


def request(*, decision="a"*32, action="RESUME_WORKER"):
    return ContinuationRequest(
        project_id="r2-atomic-workflow", decision_id="decision-"+decision,
        decision_action=action, state_version=1, master_epoch=2, daemon_epoch=3,
    )


def obs(*, url=URL, session="isolated-worker-1", unresolved=0):
    return SessionObservation(
        session_id=session, conversation_url=url, role="WORKER", generation=7,
        auth_status="AUTHENTICATED", physical_status="VERIFIED",
        auth_verification="HOST_VERIFIED", physical_verification="HOST_VERIFIED",
        response_status="UNKNOWN", unresolved_intents=unresolved,
    )


def policy(*, url=URL, active=1, operator="RUNNING"):
    return AdmissionPolicy(
        project_status="ACTIVE", operator_status=operator,
        required_role="WORKER", expected_generation=7,
        expected_conversation_url=url, active_workers=active, max_workers=2,
    )


class IsolatedAtomicContinuationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name) / "experiments"
        self.root.mkdir()
        self.folder = self.root / "r2-atomic-continuation-test"
        self.folder.mkdir()
        self.db = self.folder / "host-terminal-replay-ledger.sqlite3"

    def reserve(self, *, event="e"*32, first_seq=1, decision="a"*32,
                intent="safe-intent-1", url=URL, session="isolated-worker-1",
                key=KEY, action="RESUME_WORKER", active=1, operator="RUNNING",
                unresolved=0, now=6000, database=None):
        samples, receipts = evidence(
            url=url, session=session, intent=intent,
            event=event, first_seq=first_seq,
        )
        return reserve_verified_continuation_for_review(
            database or self.db, allowed_experiments_root=self.root,
            request=request(decision=decision, action=action),
            observation=obs(url=url, session=session, unresolved=unresolved),
            policy=policy(url=url, active=active, operator=operator),
            samples=samples, receipts=receipts, host_attestation_key=key,
            expected_intent_id=intent, now_monotonic_ms=now,
        )

    def rows(self, table):
        # Rejected proof must not even create a ledger file. A missing DB
        # therefore means exactly zero rows, not a SQLite schema error.
        if not self.db.exists():
            return 0
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_one_signed_completion_reserves_both_entities_without_send(self):
        result = self.reserve()
        self.assertEqual("RESERVED_FOR_REVIEW", result.status)
        self.assertTrue(result.committed_for_review)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.local_execution_authorized)
        self.assertTrue(result.idempotency_key.startswith("continue-"))
        self.assertEqual(1, self.rows("host_terminal_events"))
        self.assertEqual(1, self.rows("host_terminal_scope_cursors"))
        self.assertEqual(1, self.rows("host_continuation_candidates"))

    def test_restart_same_request_is_idempotent_and_cannot_recommit_event(self):
        original = self.reserve()
        repeat = self.reserve()
        self.assertEqual("ALREADY_QUEUED", repeat.status)
        self.assertEqual(original.idempotency_key, repeat.idempotency_key)
        self.assertFalse(repeat.committed_for_review)
        self.assertEqual(1, self.rows("host_terminal_events"))
        self.assertEqual(1, self.rows("host_continuation_candidates"))

    def test_changed_signed_event_cannot_duplicate_same_decision_key(self):
        self.assertEqual("RESERVED_FOR_REVIEW", self.reserve().status)
        changed = self.reserve(event="f"*32, first_seq=3)
        self.assertEqual("ALREADY_QUEUED", changed.status)
        self.assertEqual(1, self.rows("host_terminal_events"))

    def test_same_signed_event_cannot_generate_new_decision(self):
        self.reserve()
        attempted = self.reserve(decision="b"*32)
        self.assertEqual("BLOCKED", attempted.status)
        self.assertEqual("TERMINAL_EVENT_ALREADY_RESERVED", attempted.reason)
        self.assertEqual(1, self.rows("host_continuation_candidates"))

    def test_stale_sequence_cannot_create_another_continuation(self):
        self.reserve()
        blocked = self.reserve(event="b"*32, first_seq=0, decision="b"*32)
        self.assertEqual("NON_MONOTONIC_HOST_SEQUENCE", blocked.reason)
        self.assertEqual(1, self.rows("host_terminal_events"))

    def test_second_worker_distinct_verified_session_and_event(self):
        first = self.reserve()
        second = self.reserve(
            session="isolated-worker-2",
            event="d"*32,
            decision="b"*32,
            intent="safe-intent-2",
        )
        self.assertEqual("RESERVED_FOR_REVIEW", second.status)
        self.assertNotEqual(first.idempotency_key, second.idempotency_key)
        self.assertEqual(2, self.rows("host_terminal_events"))
        self.assertEqual(2, self.rows("host_continuation_candidates"))
        self.assertFalse(second.browser_send_authorized)

    def test_two_simultaneous_same_decision_writes_only_once(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: self.reserve(), (1, 2)))
        self.assertEqual(
            ["ALREADY_QUEUED", "RESERVED_FOR_REVIEW"],
            sorted(r.status for r in outcomes),
        )
        self.assertEqual(1, self.rows("host_terminal_events"))
        self.assertEqual(1, self.rows("host_continuation_candidates"))

    def test_sqlite_failure_in_candidate_insert_rolls_back_event_and_cursor(self):
        # Force the last INSERT to abort after the event and cursor INSERTs.
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.executescript("""
                CREATE TABLE host_terminal_events(
                    event_key TEXT PRIMARY KEY,scope_key TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL,receipt_sha256 TEXT NOT NULL,
                    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE host_terminal_scope_cursors(
                    scope_key TEXT PRIMARY KEY,high_sequence INTEGER NOT NULL
                );
                CREATE TABLE host_continuation_candidates(
                    idempotency_key TEXT PRIMARY KEY,event_key TEXT UNIQUE,
                    project_id TEXT,decision_id TEXT,proof_sha256 TEXT,
                    reserved_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TRIGGER reject_candidate BEFORE INSERT ON host_continuation_candidates
                BEGIN SELECT RAISE(ABORT,'synthetic candidate write failure'); END;
            """)
        denied = self.reserve()
        self.assertEqual("BLOCKED", denied.status)
        self.assertEqual("EXPERIMENT_CONTINUATION_LEDGER_UNAVAILABLE", denied.reason)
        for table in ("host_terminal_events", "host_terminal_scope_cursors",
                      "host_continuation_candidates"):
            self.assertEqual(0, self.rows(table))
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("DROP TRIGGER reject_candidate")
            db.commit()
        self.assertEqual("RESERVED_FOR_REVIEW", self.reserve().status)

    def test_missing_signed_receipts_or_key_create_no_file(self):
        rows, proof = evidence()
        bad = reserve_verified_continuation_for_review(
            self.db, allowed_experiments_root=self.root,
            request=request(), observation=obs(), policy=policy(),
            samples=rows, receipts=proof,
            host_attestation_key=b"different-test-key-wrong-and-32-bytes-2026",
            expected_intent_id="safe-intent-1", now_monotonic_ms=6000,
        )
        self.assertEqual("BLOCKED", bad.status)
        self.assertFalse(self.db.exists())

    def test_ambiguous_existing_intent_never_reserves(self):
        denied = self.reserve(unresolved=1)
        self.assertEqual("BLOCKED", denied.status)
        self.assertFalse(denied.committed_for_review)
        self.assertEqual(0, self.rows("host_terminal_events"))
        self.assertFalse(self.db.exists())

    def test_paused_operator_never_reserves(self):
        denied = self.reserve(operator="PAUSED")
        self.assertEqual("BLOCKED", denied.status)
        self.assertFalse(denied.browser_send_authorized)
        self.assertEqual(0, self.rows("host_continuation_candidates"))
        self.assertFalse(self.db.exists())

    def test_worker_capacity_fence_applies(self):
        denied = self.reserve(active=2)
        self.assertEqual("BLOCKED", denied.status)
        self.assertEqual(0, self.rows("host_terminal_events"))
        self.assertFalse(self.db.exists())

    def test_terminal_or_reconcile_action_does_not_create_ledger(self):
        for action in ("TERMINAL", "RECONCILE_AMBIGUOUS", "EMERGENCY_STOP"):
            with self.subTest(action=action):
                result = self.reserve(action=action)
                self.assertEqual("BLOCKED", result.status)
                self.assertFalse(self.db.exists())

    def test_future_or_stale_host_observation_never_reserves(self):
        stale = self.reserve(now=60000)
        self.assertEqual("BLOCKED", stale.status)
        self.assertEqual(0, self.rows("host_continuation_candidates"))
        self.assertFalse(self.db.exists())

    def test_production_path_not_accepted_or_created(self):
        production = self.root.parent / "runtime-v4" / "active"
        production.mkdir(parents=True)
        file = production / "host-terminal-replay-ledger.sqlite3"
        denied = self.reserve(database=file)
        self.assertEqual("EXPERIMENT_SCOPE_INVALID", denied.reason)
        self.assertFalse(file.exists())

    def test_untrusted_server_record_cannot_mint_host_receipts(self):
        from master_a_dynamic_v4.untrusted_turn_record_inspector import (
            inspect_untrusted_conversation_record,
        )
        message = {"author": {"role": "assistant"}, "end_turn": True,
                   "metadata": {"finish_details": {"type": "stop"}},
                   "content": {"parts": ["Finished fixture"]}}
        record = {"conversation_id": "mock-server-conversation", "current_node": "assistant",
                  "mapping": {
                      "assistant": {"parent": "user", "message": message},
                      "user": {"parent": None, "message": {"author": {"role": "user"}}},
                  }}
        observed = inspect_untrusted_conversation_record(
            record, expected_conversation_id="mock-server-conversation",
            expected_user_node_id="user",
        )
        self.assertEqual("FINISHED_UNATTESTED", observed.status)
        self.assertFalse(observed.host_terminal_event_verified)
        rows, _ = evidence()
        attempted = reserve_verified_continuation_for_review(
            self.db, allowed_experiments_root=self.root,
            request=request(), observation=obs(), policy=policy(),
            samples=rows, receipts=None, host_attestation_key=KEY,
            expected_intent_id="safe-intent-1", now_monotonic_ms=6000,
        )
        self.assertEqual("TWO_TERMINAL_RECEIPTS_REQUIRED", attempted.reason)
        self.assertFalse(self.db.exists())

    def test_protected_observer_folder_cannot_get_ledger_write(self):
        for name in ("r2-gpt-session-audit-20261009", "r2-observer-state-20261009"):
            with self.subTest(name=name):
                folder = self.root / name
                folder.mkdir()
                path = folder / "host-terminal-replay-ledger.sqlite3"
                denied = self.reserve(database=path)
                self.assertEqual("EXPERIMENT_SCOPE_INVALID", denied.reason)
                self.assertFalse(path.exists())

    def test_existing_replay_only_reservation_blocks_escalation(self):
        from master_a_dynamic_v4.isolated_host_replay_ledger import (
            reserve_terminal_receipt_for_review,
        )
        rows, receipts = evidence()
        existing = reserve_terminal_receipt_for_review(
            self.db, allowed_experiments_root=self.root,
            samples=rows, receipts=receipts, host_attestation_key=KEY,
            expected_session_id="isolated-worker-1",
            expected_conversation_url=URL, expected_binding_generation=7,
            expected_intent_id="safe-intent-1",
        )
        self.assertEqual("RESERVED_FOR_REVIEW", existing.status)
        denied = self.reserve()
        self.assertEqual("TERMINAL_EVENT_ALREADY_RESERVED", denied.reason)
        self.assertEqual(0, self.rows("host_continuation_candidates"))


if __name__ == "__main__":
    unittest.main()
