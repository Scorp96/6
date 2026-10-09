from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import contextlib
import os
import pathlib
import sqlite3
import tempfile
import unittest

from isolated_loopback_poll_journal_v4 import (
    commit_one_nonterminal_local_sample,
)
from chrome_use_loopback_observer_v4 import LocalChromeReadObservation

NAME="scorp-r2-isolated-loopback-journal-test"
START=datetime(2026,10,9,4,0,0,tzinfo=timezone.utc)


def obs(session_count=2, tab_count=1, **kwargs):
    basic=dict(
        status="LOCAL_STATE_OBSERVED_UNATTESTED",
        reason="LAST_OBSERVED_BROWSER_COUNTS_ONLY_NO_GPT_TERMINAL_PROOF",
        session_count=session_count,
        tab_count=tab_count,
        status_endpoint_readable=True,
        all_three_endpoints_readable=True,
    )
    basic.update(kwargs)
    return LocalChromeReadObservation(**basic)


class LoopbackPollJournalTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=pathlib.Path(folder.name) / "experiments"
        self.root.mkdir()
        self.folder=self.root/"r2-loopback-poll-canary-fixture"
        self.folder.mkdir()
        self.db=self.folder/"local-poll-journal.sqlite3"

    def commit(self, observation=None, *, now=START, cadence=15, db=None,
               namespace=NAME):
        return commit_one_nonterminal_local_sample(
            observation if observation is not None else obs(),
            isolated_namespace=namespace,
            database=db if db is not None else self.db,
            allowed_isolated_root=self.root,
            cadence_minutes=cadence,
            observed_utc=now,
        )

    def row_count(self):
        with contextlib.closing(sqlite3.connect(self.db)) as con:
            return con.execute("SELECT COUNT(*) FROM local_poll_sample").fetchone()[0]

    def assert_no_authority(self, result):
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.worker_wake_authorized)
        self.assertFalse(result.production_cutover_authorized)
        self.assertFalse(result.native_final_event_verified)
        self.assertEqual(0,result.local_model_calls)

    def test_first_zero_model_observation_is_never_a_wake(self):
        r=self.commit()
        self.assertEqual("LOCAL_NONTERMINAL_SAMPLE_COMMITTED",r.status)
        self.assertEqual("FIRST_LOCAL_STATE_UNATTESTED",r.category)
        self.assertTrue(r.sample_written)
        self.assertEqual(1,self.row_count())
        self.assert_no_authority(r)
        raw=self.db.read_bytes()
        for secret in (b"chatgpt.com",b"about:blank",b"PRIVATE",NAME.encode()):
            self.assertNotIn(secret,raw)

    def test_duplicate_timestamp_refuses_second_insert(self):
        self.commit()
        r=self.commit()
        self.assertEqual("CLOCK_REWIND_OR_DUPLICATE",r.reason)
        self.assertEqual(1,self.row_count())
        self.assert_no_authority(r)

    def test_earlier_wall_clock_refuses_replay(self):
        self.commit()
        r=self.commit(now=START-timedelta(seconds=1))
        self.assertEqual("CLOCK_REWIND_OR_DUPLICATE",r.reason)
        self.assertEqual(1,self.row_count())

    def test_early_repeat_not_eligible_even_across_slot_boundary(self):
        self.commit()
        result=self.commit(now=START+timedelta(minutes=14,seconds=59))
        self.assertEqual("POLL_CADENCE_NOT_ELAPSED",result.reason)
        self.assertEqual(1,self.row_count())

    def test_exact_15minute_mark_stable_is_nonterminal(self):
        self.commit()
        r=self.commit(now=START+timedelta(minutes=15))
        self.assertEqual("LOCAL_NONTERMINAL_SAMPLE_COMMITTED",r.status)
        self.assertEqual("COUNTS_STABLE_UNATTESTED",r.category)
        self.assertEqual(2,self.row_count())
        self.assert_no_authority(r)

    def test_changed_counts_never_trigger_a_model_wake(self):
        self.commit()
        r=self.commit(obs(4,5),now=START+timedelta(minutes=25))
        self.assertEqual("COUNTS_CHANGED_UNATTESTED",r.category)
        self.assertEqual(2,self.row_count())
        self.assert_no_authority(r)

    def test_25minute_minimum_cannot_be_shortened_to_15(self):
        self.assertTrue(self.commit(cadence=25).sample_written)
        too_soon=self.commit(cadence=15,now=START+timedelta(minutes=16))
        self.assertEqual("POLL_CADENCE_NOT_ELAPSED",too_soon.reason)
        ok=self.commit(cadence=15,now=START+timedelta(minutes=25))
        self.assertEqual("LOCAL_NONTERMINAL_SAMPLE_COMMITTED",ok.status)
        self.assert_no_authority(ok)

    def test_current_larger_cadence_cannot_be_bypassed(self):
        self.commit(cadence=15)
        r=self.commit(cadence=25,now=START+timedelta(minutes=20))
        self.assertEqual("POLL_CADENCE_NOT_ELAPSED",r.reason)

    def test_concurrent_one_slot_has_single_committer(self):
        def once(_):return self.commit()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results=list(executor.map(once,range(4)))
        self.assertEqual(1,sum(x.sample_written for x in results))
        self.assertEqual(1,self.row_count())
        self.assertTrue(all(not x.worker_wake_authorized for x in results))

    def test_corrupted_db_fails_without_source_credentials(self):
        self.db.write_bytes(b"corrupted db SECRET_PRIVATE_STRING")
        r=self.commit()
        self.assertEqual("DURABLE_LOCAL_POLL_UNAVAILABLE",r.reason)
        self.assertNotIn("SECRET",repr(r))
        self.assert_no_authority(r)

    def test_blocked_http_observation_is_not_saved(self):
        source=obs(status="BLOCKED")
        r=self.commit(source)
        self.assertEqual("LOCAL_SOURCE_UNVERIFIED_NO_JOURNAL",r.reason)
        self.assertFalse(self.db.exists())
        self.assert_no_authority(r)

    def test_forged_host_final_flag_is_rejected(self):
        for source in (
            obs(native_final_event_verified=True),
            obs(browser_send_authorized=True),
            obs(local_execution_authorized=True),
            obs(authentication_verified=True),
            obs(local_model_calls=1),
        ):
            with self.subTest(source=source):
                r=self.commit(source)
                self.assertEqual("LOCAL_SOURCE_UNVERIFIED_NO_JOURNAL",r.reason)
                self.assertFalse(self.db.exists())

    def test_boolean_nan_or_out_of_range_count_is_rejected(self):
        for count in (True,False,-1,257,"2",None,1.25):
            with self.subTest(count=count):
                r=self.commit(obs(count,1))
                self.assertEqual("LOCAL_SOURCE_UNVERIFIED_NO_JOURNAL",r.reason)
                self.assertFalse(self.db.exists())

    def test_missing_local_http_endpoints_cannot_be_recorded_healthy(self):
        for data in (
            {"status_endpoint_readable":False},
            {"all_three_endpoints_readable":False},
        ):
            with self.subTest(data=data):
                r=self.commit(obs(**data))
                self.assertEqual("LOCAL_SOURCE_UNVERIFIED_NO_JOURNAL",r.reason)
                self.assertFalse(self.db.exists())

    def test_wrong_session_name_or_cadence_blocks_before_disk(self):
        for name in ("master",None,"", "scorp-r1"):
            r=self.commit(namespace=name)
            self.assertEqual("LOCAL_POLL_CONTRACT_INVALID",r.reason)
        for cadence in (0,5,10,16,30,True,False,"15"):
            r=self.commit(cadence=cadence)
            self.assertEqual("LOCAL_POLL_CONTRACT_INVALID",r.reason)
        self.assertFalse(self.db.exists())

    def test_naive_datetime_invalid_without_modifying_db(self):
        naive=START.replace(tzinfo=None)
        r=self.commit(now=naive)
        self.assertEqual("UTC_SAMPLE_TIMESTAMP_INVALID",r.reason)
        self.assertFalse(self.db.exists())

    def test_timestamp_outside_safe_range_not_saved(self):
        for timestamp in (
            datetime(2001,1,1,tzinfo=timezone.utc),
            datetime(2300,1,1,tzinfo=timezone.utc),
        ):
            with self.subTest(timestamp=timestamp):
                r=self.commit(now=timestamp)
                self.assertEqual("UTC_SAMPLE_TIMESTAMP_OUTSIDE_SAFE_RANGE",r.reason)
                self.assertFalse(self.db.exists())

    def test_frozen_source_and_observer_worktrees_never_written(self):
        for name in ("r2-host-terminal-security-20261009",
                     "r2-gpt-session-audit-20261009",
                     "r2-observer-state-20261009"):
            with self.subTest(name=name):
                folder=self.root/name
                folder.mkdir()
                target=folder/"local-poll-journal.sqlite3"
                result=self.commit(db=target)
                self.assertEqual("ISOLATED_POLL_DB_SCOPE_INVALID",result.reason)
                self.assertFalse(target.exists())

    def test_external_symlink_directory_is_rejected(self):
        outsider=pathlib.Path(self.root.parent)/"outside-root"
        outsider.mkdir()
        alias=self.root/"r2-loopback-poll-escape"
        try:
            alias.symlink_to(outsider,target_is_directory=True)
        except OSError:
            self.skipTest("OS symlink unavailable")
        r=self.commit(db=alias/"local-poll-journal.sqlite3")
        self.assertEqual("ISOLATED_POLL_DB_SCOPE_INVALID",r.reason)
        self.assertFalse((outsider/"local-poll-journal.sqlite3").exists())

    def test_hardlink_protected_from_cross_directory_writes(self):
        self.db.write_bytes(b"fake")
        link=self.folder/"alias"
        try:
            os.link(self.db,link)
        except OSError:
            self.skipTest("hardlinks unsupported")
        r=self.commit()
        self.assertEqual("ISOLATED_POLL_DB_SCOPE_INVALID",r.reason)

    def test_reopens_same_ledger_across_process_equivalent_instances(self):
        first=self.commit()
        self.assertTrue(first.sample_written)
        again=self.commit(obs(5,1),now=START+timedelta(minutes=25))
        self.assertEqual("COUNTS_CHANGED_UNATTESTED",again.category)
        self.assertEqual(2,self.row_count())
        self.assert_no_authority(again)


if __name__=="__main__":
    unittest.main()
