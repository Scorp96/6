from __future__ import annotations

import contextlib
import dataclasses
import pathlib
import sqlite3
import tempfile
import threading
import unittest

from master_a_dynamic_v4.local_binding_preflight import LocalBindingPreflight
from master_a_dynamic_v4.local_tick_observer import run_tick


def blocked(reason="MASTER_ROTATION_CONFLICT_UNRESOLVED", **kwargs):
    return dataclasses.replace(
        LocalBindingPreflight(
            status="BLOCKED",
            reason=reason,
            unresolved_intents=1,
            rotation_conflict=True,
            live_sessions=1,
            chatgpt_tabs=1,
            matching_master_tabs=0,
        ), **kwargs,
    )


class LocalTickObserverTests(unittest.TestCase):
    def fixture(self,minutes=15):
        td=tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root=pathlib.Path(td.name)
        return root/"observer.sqlite3",minutes

    def test_first_tick_is_due_and_always_no_send(self):
        db,minutes=self.fixture()
        calls=[]
        r=run_tick(db,interval_minutes=minutes,now_ms=1000,inspect=lambda:(calls.append(1),blocked())[1])
        self.assertEqual("OBSERVED",r.status)
        self.assertEqual(901000,r.next_due_ms)
        self.assertEqual(1,r.event_seq)
        self.assertTrue(r.changed)
        self.assertEqual([1],calls)
        self.assertFalse(r.browser_send_authorized)
        self.assertFalse(r.browser_adoption_authorized)

    def test_15_minute_cadence_skips_without_probing(self):
        db,minutes=self.fixture()
        counter=[]
        def probe():
            counter.append(1)
            return blocked()
        run_tick(db,interval_minutes=15,now_ms=0,inspect=probe)
        skip=run_tick(db,interval_minutes=15,now_ms=899999,inspect=probe)
        self.assertEqual("SKIPPED",skip.status)
        self.assertEqual("NOT_DUE",skip.reason)
        self.assertEqual(1,len(counter))
        due=run_tick(db,interval_minutes=15,now_ms=900000,inspect=probe)
        self.assertEqual("OBSERVED",due.status)
        self.assertEqual(2,len(counter))
        self.assertFalse(due.changed)

    def test_25_minute_cadence(self):
        db,_=self.fixture()
        count=[]
        def observe():
            count.append(1)
            return blocked()
        run_tick(db,interval_minutes=25,now_ms=0,inspect=observe)
        self.assertEqual("SKIPPED",run_tick(db,interval_minutes=25,now_ms=1499999,inspect=observe).status)
        self.assertEqual("OBSERVED",run_tick(db,interval_minutes=25,now_ms=1500000,inspect=observe).status)
        self.assertEqual(2,len(count))

    def test_interval_switch_is_blocked_without_running_browser(self):
        db,_=self.fixture()
        run_tick(db,interval_minutes=15,now_ms=0,inspect=blocked)
        calls=[]
        r=run_tick(db,interval_minutes=25,now_ms=900001,inspect=lambda:calls.append(1))
        self.assertEqual("INTERVAL_CONFIGURATION_CONFLICT",r.reason)
        self.assertEqual([],calls)

    def test_clock_backwards_fails_closed(self):
        db,_=self.fixture()
        run_tick(db,interval_minutes=15,now_ms=5000,inspect=blocked)
        r=run_tick(db,interval_minutes=15,now_ms=4999,inspect=lambda:None)
        self.assertEqual("WALL_CLOCK_REGRESSED",r.reason)

    def test_repeated_tick_at_exact_same_time_does_not_duplicate(self):
        db,_=self.fixture()
        calls=[]
        def observe():
            calls.append(1);return blocked()
        a=run_tick(db,interval_minutes=15,now_ms=0,inspect=observe)
        b=run_tick(db,interval_minutes=15,now_ms=0,inspect=observe)
        self.assertEqual(1,a.event_seq)
        self.assertEqual("SKIPPED",b.status)
        self.assertEqual([1],calls)

    def test_changed_event_only_when_fingerprint_changes(self):
        db,_=self.fixture()
        r1=run_tick(db,interval_minutes=15,now_ms=0,inspect=blocked)
        r2=run_tick(db,interval_minutes=15,now_ms=900000,inspect=blocked)
        r3=run_tick(db,interval_minutes=15,now_ms=1800000,
                    inspect=lambda:blocked("CANONICAL_MASTER_TAB_NOT_FOUND"))
        self.assertEqual((True,False,True),(r1.changed,r2.changed,r3.changed))

    def test_reopen_database_preserves_due_and_sequence(self):
        db,_=self.fixture()
        first=run_tick(db,interval_minutes=15,now_ms=0,inspect=blocked)
        result=run_tick(db,interval_minutes=15,now_ms=900000,inspect=blocked)
        self.assertEqual((1,2),(first.event_seq,result.event_seq))
        with contextlib.closing(sqlite3.connect(db)) as c:
            events=c.execute("SELECT COUNT(*) FROM observer_events").fetchone()[0]
            self.assertEqual(2,events)

    def test_incomplete_prior_reservation_blocks_no_retry(self):
        db,_=self.fixture()
        run_tick(db,interval_minutes=15,now_ms=0,inspect=blocked)
        with contextlib.closing(sqlite3.connect(db)) as c,c:
            c.execute("UPDATE observer_schedule SET pending_seq=999 WHERE id=1")
        called=[]
        r=run_tick(db,interval_minutes=15,now_ms=900001,inspect=lambda:called.append(1))
        self.assertEqual("INCOMPLETE_PREVIOUS_TICK",r.reason)
        self.assertFalse(r.browser_send_authorized)
        self.assertEqual([],called)

    def test_callback_failure_persisted_as_redacted_blocked_not_exception(self):
        db,_=self.fixture()
        def unexpected():
            raise ValueError("secret https://chatgpt.com/c/private")
        r=run_tick(db,interval_minutes=15,now_ms=0,inspect=unexpected)
        self.assertEqual("OBSERVER_READ_FAILED",r.reason)
        with contextlib.closing(sqlite3.connect(db)) as c:
            stored=c.execute("SELECT reason FROM observer_events").fetchone()[0]
        self.assertEqual("OBSERVER_READ_FAILED",stored)

    def test_response_with_raw_url_cannot_become_event_reason(self):
        db,_=self.fixture()
        evil=blocked("SECRET https://chatgpt.com/c/private")
        r=run_tick(db,interval_minutes=15,now_ms=0,inspect=lambda:evil)
        self.assertEqual("UNCLASSIFIED_OBSERVATION",r.reason)

    def test_unexpected_send_authority_rejected(self):
        db,_=self.fixture()
        obj=blocked(browser_send_authorized=True)
        r=run_tick(db,interval_minutes=15,now_ms=0,inspect=lambda:obj)
        self.assertEqual("UNEXPECTED_BROWSER_AUTHORITY",r.reason)
        self.assertFalse(r.browser_send_authorized)

    def test_unexpected_adoption_authority_rejected(self):
        db,_=self.fixture()
        obj=blocked(browser_adoption_authorized=True)
        r=run_tick(db,interval_minutes=15,now_ms=0,inspect=lambda:obj)
        self.assertEqual("UNEXPECTED_BROWSER_AUTHORITY",r.reason)
        self.assertFalse(r.browser_adoption_authorized)

    def test_invalid_clock_and_interval_fail_before_ledger_creation(self):
        db,_=self.fixture()
        for minute,epoch in ((10,0),(15,-1),(True,0),(25,False)):
            with self.subTest(minute=minute,epoch=epoch):
                with self.assertRaises(ValueError):
                    run_tick(db,interval_minutes=minute,now_ms=epoch,inspect=blocked)
        self.assertFalse(db.exists())

    def test_missing_workspace_does_not_make_directory(self):
        db,_=self.fixture()
        db=db.parent/"not-created"/"observer.sqlite3"
        with self.assertRaisesRegex(ValueError,"OBSERVER_PARENT_DIRECTORY_MISSING"):
            run_tick(db,interval_minutes=15,now_ms=0,inspect=blocked)
        self.assertFalse(db.parent.exists())

    def test_pending_while_another_process_is_observing_never_probes(self):
        db,_=self.fixture()
        ready=threading.Event()
        release=threading.Event()
        first=[]
        def hold():
            ready.set()
            self.assertTrue(release.wait(5))
            return blocked()
        def worker():
            first.append(run_tick(db,interval_minutes=15,now_ms=0,inspect=hold))
        t=threading.Thread(target=worker)
        t.start()
        self.assertTrue(ready.wait(5))
        calls=[]
        second=run_tick(db,interval_minutes=15,now_ms=0,
                        inspect=lambda:calls.append("unsafe"))
        self.assertEqual("INCOMPLETE_PREVIOUS_TICK",second.reason)
        self.assertEqual([],calls)
        release.set();t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual("OBSERVED",first[0].status)

    def test_event_history_retention_bounded(self):
        db,_=self.fixture()
        for step in range(505):
            out=run_tick(db,interval_minutes=15,now_ms=step*900000,inspect=blocked)
            self.assertEqual("OBSERVED",out.status)
        with contextlib.closing(sqlite3.connect(db)) as c:
            count=c.execute("SELECT COUNT(*) FROM observer_events").fetchone()[0]
        self.assertEqual(500,count)


if __name__=="__main__":
    unittest.main()
