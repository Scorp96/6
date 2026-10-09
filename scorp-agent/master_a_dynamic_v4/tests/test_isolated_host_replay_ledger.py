from __future__ import annotations

from dataclasses import asdict, replace
import contextlib
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.host_terminal_receipt import HostTerminalReceipt
from master_a_dynamic_v4.turn_completion_evidence import TurnSample
from master_a_dynamic_v4.isolated_host_replay_ledger import reserve_terminal_receipt_for_review

URL = "https://chatgpt.com/c/isolated-event-test"
KEY = b"the-unit-test-only-host-key-32-bytes-minimum-20261009"
RESP = "a" * 64


def signed(receipt, key=KEY):
    value=asdict(receipt)
    value.pop("signature")
    digest=hmac.new(key,json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
    return replace(receipt,signature=digest)


def pair(*, event="1"*32, seq1=1, seq2=2, session="host-session-a",
         generation=3, intent="intent-x", url=URL):
    samples=[]
    receipts=[]
    for ms,seq in ((1000,seq1),(5000,seq2)):
        row=TurnSample(
            session_id=session,conversation_url=url,binding_generation=generation,
            intent_id=intent,sampled_at_ms=ms,generating=False,tool_pending=False,
            response_sha256=RESP,response_intent_verified=True,
            finish_event="TURN_FINAL_CONFIRMED",finish_event_provenance="HOST_VERIFIED",
        )
        samples.append(row)
        rec=HostTerminalReceipt(
            protocol="scorp.browser-terminal-receipt/1",event_id=event,sequence=seq,
            session_id=session,conversation_url=url,binding_generation=generation,
            intent_id=intent,sampled_at_ms=ms,response_sha256=RESP,generating=False,
            tool_pending=False,response_intent_verified=True,
            terminal_event="TURN_FINAL_CONFIRMED",
        )
        receipts.append(signed(rec))
    return samples,receipts


class ReplayLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)/"experiments"
        self.root.mkdir()
        self.folder=self.root/"r2-terminal-evidence-test"
        self.folder.mkdir()
        self.path=self.folder/"host-terminal-replay-ledger.sqlite3"

    def call(self, *, event="1"*32, seq1=1, seq2=2, session="host-session-a",
             generation=3, intent="intent-x", url=URL, key=KEY,
             root=None, database=None, expected_url=None):
        samples,receipts=pair(event=event,seq1=seq1,seq2=seq2,
                              session=session,generation=generation,intent=intent,url=url)
        return reserve_terminal_receipt_for_review(
            database or self.path,allowed_experiments_root=root or self.root,
            samples=samples,receipts=receipts,host_attestation_key=key,
            expected_session_id=session,
            expected_conversation_url=url if expected_url is None else expected_url,
            expected_binding_generation=generation,expected_intent_id=intent,
        )

    def test_good_signed_pair_reserved_only_for_review(self):
        result=self.call()
        self.assertEqual("RESERVED_FOR_REVIEW",result.status)
        self.assertTrue(result.terminal_event_reserved_for_review)
        self.assertFalse(result.browser_send_authorized)
        self.assertTrue(self.path.exists())

    def test_same_event_after_process_restart_is_deduplicated(self):
        self.assertEqual("RESERVED_FOR_REVIEW",self.call().status)
        self.assertEqual("ALREADY_RESERVED",self.call().status)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(1,db.execute("SELECT COUNT(*) FROM host_terminal_events").fetchone()[0])

    def test_canonical_url_alias_cannot_re_reserve_signed_terminal_event(self):
        self.assertEqual("RESERVED_FOR_REVIEW", self.call().status)
        # The signed receipt and samples keep their canonical URL. Only the
        # caller-supplied expected URL varies, as happens across handoffs.
        padded = self.call(expected_url="  " + URL + "  ")
        self.assertEqual("ALREADY_RESERVED", padded.status)
        self.assertFalse(padded.browser_send_authorized)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM host_terminal_events").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM host_terminal_scope_cursors").fetchone()[0])

    def test_padded_expected_url_does_not_reset_sequence_cursor(self):
        self.assertEqual("RESERVED_FOR_REVIEW", self.call().status)
        stale = self.call(event="2"*32, seq1=0, seq2=1, expected_url=" " + URL)
        self.assertEqual("BLOCKED", stale.status)
        self.assertEqual("NON_MONOTONIC_HOST_SEQUENCE", stale.reason)
        self.assertFalse(stale.browser_send_authorized)

    def test_new_event_with_stale_sequence_is_rejected(self):
        self.call()
        old=self.call(event="2"*32,seq1=0,seq2=1)
        self.assertEqual("BLOCKED",old.status)
        self.assertEqual("NON_MONOTONIC_HOST_SEQUENCE",old.reason)

    def test_increasing_sequence_for_new_event_is_possible_without_sending(self):
        self.call()
        nxt=self.call(event="2"*32,seq1=3,seq2=4,intent="intent-y")
        self.assertEqual("RESERVED_FOR_REVIEW",nxt.status)
        self.assertFalse(nxt.browser_send_authorized)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(2,db.execute("SELECT COUNT(*) FROM host_terminal_events").fetchone()[0])

    def test_separate_session_scope_has_independent_sequence(self):
        self.call()
        result=self.call(event="3"*32,session="host-session-b",intent="intent-z")
        self.assertEqual("RESERVED_FOR_REVIEW",result.status)

    def test_outside_experiments_cannot_create_database(self):
        forbidden=pathlib.Path(self.tmp.name)/"runtime-v4"/"active"
        forbidden.mkdir(parents=True)
        other=forbidden/"host-terminal-replay-ledger.sqlite3"
        result=self.call(database=other)
        self.assertEqual("BLOCKED",result.status)
        self.assertEqual("EXPERIMENT_SCOPE_INVALID",result.reason)
        self.assertFalse(other.exists())

    def test_wrong_root_does_not_allow_db_creation(self):
        result=self.call(root=self.folder)
        self.assertEqual("EXPERIMENT_SCOPE_INVALID",result.reason)
        self.assertFalse(self.path.exists())

    def test_invalid_signature_or_missing_key_creates_no_ledger(self):
        result=self.call(key=b"wrong but correctly sized host key-123456789")
        self.assertEqual("BLOCKED",result.status)
        self.assertIn("HOST_RECEIPT_SIGNATURE_INVALID",result.reason)
        self.assertFalse(self.path.exists())

    def test_recovery_after_previous_transaction_blocks_exact_replay(self):
        self.call()
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual("ok",db.execute("PRAGMA integrity_check").fetchone()[0])
        repeated=self.call()
        self.assertEqual("ALREADY_RESERVED",repeated.status)

    def test_hardlinked_existing_sqlite_cannot_alias_production(self):
        # Only a temporary *fixture* represents the protected production DB.
        production=pathlib.Path(self.tmp.name)/"fake-authoritative.sqlite3"
        with contextlib.closing(sqlite3.connect(production)) as db:
            db.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO sentinel(id) VALUES(42)")
            db.commit()
        try:
            os.link(production, self.path)
        except (OSError, NotImplementedError):
            self.skipTest("hardlinks unavailable on this platform")
        original=production.read_bytes()
        result=self.call()
        self.assertEqual("EXPERIMENT_SCOPE_INVALID", result.reason)
        self.assertEqual(original, production.read_bytes())

    def test_pinned_active_observer_directories_never_change(self):
        for name in ("r2-gpt-session-audit-20261009", "r2-observer-state-20261009"):
            with self.subTest(name=name):
                folder=self.root/name
                folder.mkdir()
                db=folder/"host-terminal-replay-ledger.sqlite3"
                result=self.call(database=db)
                self.assertEqual("EXPERIMENT_SCOPE_INVALID", result.reason)
                self.assertFalse(db.exists())

    def test_symlinked_isolation_folder_rejected(self):
        source=self.root/"r2-source"
        source.mkdir()
        link=self.root/"r2-link"
        try:
            link.symlink_to(source, target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest("symlinks unavailable on this Windows test runner")
        path=link/"host-terminal-replay-ledger.sqlite3"
        result=self.call(database=path)
        self.assertEqual("EXPERIMENT_SCOPE_INVALID",result.reason)


if __name__=="__main__":
    unittest.main()
