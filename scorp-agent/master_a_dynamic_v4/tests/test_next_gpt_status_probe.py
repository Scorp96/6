"""Contract tests for the model-free, no-browser next-GPT bootstrap probe."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.next_gpt_status_probe import main, observe

NOW = dt.datetime(2026, 10, 9, 1, 0, tzinfo=dt.timezone.utc)
SECRET = "https://chatgpt.com/c/secret-session-id-will-not-escape"
BEARER = "very-private-browser-token-not-for-github"


class HandoffProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def build_files(self, *, reason="MASTER_ROTATION_CONFLICT_UNRESOLVED"):
        recent_ms = int((NOW - dt.timedelta(minutes=5)).timestamp() * 1000)
        next_due_ms = int((NOW + dt.timedelta(minutes=10)).timestamp() * 1000)
        obs = self.root / "experiments/r2-observer-state-20261009/scorp-readonly-observer.sqlite3"
        obs.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(obs)) as c, c:
            c.executescript("""
                CREATE TABLE observer_schedule (
                  id INTEGER, interval_minutes INTEGER,
                  last_reserved_ms INTEGER, next_due_ms INTEGER,
                  pending_seq INTEGER);
                CREATE TABLE observer_events (
                  seq INTEGER, observed_ms INTEGER, status TEXT, reason TEXT,
                  changed INTEGER);
            """)
            c.execute("INSERT INTO observer_schedule VALUES(1,15,?,?,NULL)",(recent_ms,next_due_ms))
            c.execute("INSERT INTO observer_events VALUES(1,?,'BLOCKED',?,1)",(recent_ms,reason))
        r1 = self.root / "runtime-v4/active/state.sqlite3"
        r1.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(r1)) as c, c:
            c.executescript("""
                CREATE TABLE project_state(status TEXT,phase TEXT,state_version INTEGER);
                CREATE TABLE daemon_leases(
                    daemon_epoch INTEGER,lease_status TEXT,
                    heartbeat_at TEXT,lease_until TEXT);
                CREATE TABLE daemon_supervision(
                    recovery_count INTEGER,consecutive_failures INTEGER,
                    circuit_state TEXT,last_failure_at TEXT);
                CREATE TABLE action_intents(state TEXT);
                CREATE TABLE browser_bindings(binding TEXT);
                INSERT INTO project_state VALUES('ACTIVE','BOOTSTRAP',0);
                INSERT INTO daemon_leases VALUES(44,'ACTIVE',
                    '2026-10-09T00:59:59Z','2026-10-09T01:00:29Z');
                INSERT INTO daemon_supervision VALUES(41,0,'CLOSED',NULL);
                INSERT INTO action_intents VALUES('BLOCKED_AMBIGUOUS');
            """)
        gui = self.root / "chatgpt-gui-bridge-state/health.json"
        gui.parent.mkdir(parents=True, exist_ok=True)
        gui.write_text(json.dumps({
            "status": "ERROR",
            "error": "MASTER_CONVERSATION_ROTATION_REQUIRED " + SECRET + " " + BEARER,
        }), encoding="utf-8")
        driver = self.root / "state-v3/active/chrome-use-driver-v3.json"
        driver.parent.mkdir(parents=True, exist_ok=True)
        driver.write_text(json.dumps({
            "sessions": {},
            "conversation_url": SECRET,
            "access_token": BEARER,
        }), encoding="utf-8")
        return obs, r1, gui, driver

    def test_missing_files_fail_closed_and_do_not_create_databases(self):
        result = observe(self.root, now_utc=NOW)
        self.assertEqual("MISSING", result["r1"]["status"])
        self.assertEqual("MISSING", result["observer_15m"]["status"])
        self.assertIn("R1_AUTHORITY_UNAVAILABLE", result["blockers"])
        self.assertFalse(result["browser_send_authorized"])
        self.assertFalse(result["local_execution_authorized"])
        self.assertFalse((self.root / "runtime-v4").exists())

    def test_sanitized_authoritative_status_never_leaks_remote_data(self):
        paths = self.build_files()
        result = observe(self.root, now_utc=NOW)
        self.assertEqual("READ_ONLY_OK",result["r1"]["status"])
        self.assertEqual(1,result["r1"]["ambiguous_intents"])
        self.assertEqual(44,result["r1"]["daemon_epoch"])
        self.assertEqual(41,result["r1"]["daemon_recovery_count"])
        self.assertTrue(result["r1"]["daemon_lease_current"])
        self.assertEqual("OBSERVED",result["observer_15m"]["status"])
        self.assertEqual("ok",result["observer_15m"]["db_integrity"])
        self.assertEqual(1,result["observer_15m"]["event_count"])
        self.assertEqual(15,result["observer_15m"]["due_interval_minutes"])
        self.assertIn("AMBIGUOUS_SUBMIT_UNRESOLVED",result["blockers"])
        self.assertIn("MASTER_CONVERSATION_ROTATION_REQUIRED",result["blockers"])
        result_text=json.dumps(result)
        self.assertNotIn(SECRET,result_text)
        self.assertNotIn(BEARER,result_text)
        self.assertNotIn(str(self.root),result_text)
        self.assertFalse(result["browser_send_authorized"])

    def test_probe_does_not_mutate_even_isolated_sqlite(self):
        paths = self.build_files()
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        observe(self.root, now_utc=NOW)
        after = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        self.assertEqual(before, after)

    def test_malicious_reason_is_redacted(self):
        self.build_files(reason="ROTATION_" + SECRET + " " + BEARER)
        result = observe(self.root, now_utc=NOW)
        self.assertEqual("REDACTED",result["observer_15m"]["last_reason"])
        self.assertNotIn(SECRET,json.dumps(result))

    def test_corrupt_databases_fenced_without_raise(self):
        o,r,*_ = self.build_files()
        o.write_bytes(b"not a database")
        r.write_bytes(b"not a database")
        result=observe(self.root,now_utc=NOW)
        self.assertEqual("UNREADABLE",result["r1"]["status"])
        self.assertEqual("UNREADABLE",result["observer_15m"]["status"])
        self.assertIn("R1_AUTHORITY_UNAVAILABLE",result["blockers"])

    def test_missing_driver_not_implied_authenticated(self):
        self.build_files()
        (self.root/"state-v3/active/chrome-use-driver-v3.json").unlink()
        result=observe(self.root,now_utc=NOW)
        self.assertEqual("UNAVAILABLE",result["v3_driver"]["status"])
        self.assertIn("PHYSICAL_GPT_SESSION_UNVERIFIED",result["blockers"])

    def test_legacy_v3_turns_and_conversations_are_readable_not_live(self):
        self.build_files()
        driver = self.root / "state-v3/active/chrome-use-driver-v3.json"
        driver.write_text(json.dumps({
            "protocol_version": "legacy-driver/1",
            "turns": {"turn-hidden": {"url": SECRET}},
            "conversations": {SECRET: {"token": BEARER}},
        }), encoding="utf-8")
        result = observe(self.root, now_utc=NOW)
        self.assertEqual("LEGACY_STATE_READABLE", result["v3_driver"]["status"])
        self.assertEqual(1, result["v3_driver"]["legacy_turn_count"])
        self.assertEqual(1, result["v3_driver"]["legacy_conversation_count"])
        self.assertIsNone(result["v3_driver"]["physical_sessions_count"])
        self.assertFalse(result["v3_driver"]["live_session_verified"])
        self.assertIn("PHYSICAL_GPT_SESSION_UNVERIFIED", result["blockers"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(BEARER, json.dumps(result))
        self.assertFalse(result["browser_send_authorized"])

    def test_serialized_sessions_never_claim_live_authentication(self):
        self.build_files()
        driver = self.root / "state-v3/active/chrome-use-driver-v3.json"
        driver.write_text(json.dumps({
            "sessions": {"hidden-session": {"url": SECRET, "token": BEARER}}
        }), encoding="utf-8")
        result = observe(self.root, now_utc=NOW)
        self.assertEqual("READ_ONLY_OK", result["v3_driver"]["status"])
        self.assertEqual(1, result["v3_driver"]["physical_sessions_count"])
        self.assertFalse(result["v3_driver"]["live_session_verified"])
        self.assertIn("PHYSICAL_GPT_SESSION_UNVERIFIED", result["blockers"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_unrecognized_v3_layout_remains_unverified(self):
        self.build_files()
        driver = self.root / "state-v3/active/chrome-use-driver-v3.json"
        driver.write_text(json.dumps({"unknown": BEARER}), encoding="utf-8")
        result = observe(self.root, now_utc=NOW)
        self.assertEqual("UNRECOGNIZED_LAYOUT", result["v3_driver"]["status"])
        self.assertIn("PHYSICAL_GPT_SESSION_UNVERIFIED", result["blockers"])
        self.assertNotIn(BEARER, json.dumps(result))

    def test_stale_observer_is_detected_even_with_integrity_ok(self):
        self.build_files()
        result = observe(self.root, now_utc=NOW + dt.timedelta(minutes=45))
        self.assertEqual("ok", result["observer_15m"]["db_integrity"])
        self.assertEqual("STALE", result["observer_15m"]["status"])
        self.assertIn("ISOLATED_OBSERVER_STATE_UNVERIFIED", result["blockers"])
        self.assertFalse(result["browser_send_authorized"])

    def test_future_dated_observer_event_is_not_trusted(self):
        paths = self.build_files()
        with contextlib.closing(sqlite3.connect(paths[0])) as conn, conn:
            conn.execute("UPDATE observer_events SET observed_ms=?",
                         (int((NOW + dt.timedelta(minutes=8)).timestamp()*1000),))
        result = observe(self.root, now_utc=NOW)
        self.assertEqual("CLOCK_SKEW", result["observer_15m"]["status"])
        self.assertIn("ISOLATED_OBSERVER_STATE_UNVERIFIED", result["blockers"])

    def test_expired_daemon_lease_does_not_look_active(self):
        self.build_files()
        result = observe(self.root, now_utc=NOW + dt.timedelta(minutes=5))
        self.assertEqual("READ_ONLY_OK", result["r1"]["status"])
        self.assertEqual("ACTIVE", result["r1"]["daemon_lease_status"])
        self.assertFalse(result["r1"]["daemon_lease_current"])
        self.assertIn("R1_DAEMON_LEASE_UNVERIFIED", result["blockers"])
        self.assertFalse(result["local_execution_authorized"])

    def test_cli_prints_one_json_record_and_no_browser_authority(self):
        self.build_files()
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):
            returned=main(["--scorp-root",str(self.root)])
        self.assertEqual(0,returned)
        lines=stream.getvalue().splitlines()
        self.assertEqual(1,len(lines))
        result=json.loads(lines[0])
        self.assertEqual("scorp.next-gpt-status-readonly/1",result["protocol_version"])
        self.assertFalse(result["browser_send_authorized"])
        self.assertEqual("NONE",result["production_writes"])

    def test_reject_naive_utc_clock(self):
        with self.assertRaisesRegex(ValueError,"UTC_CLOCK_REQUIRED"):
            observe(self.root,now_utc=dt.datetime(2026,10,9,0,0))

    def test_blank_gui_is_unavailable(self):
        self.build_files()
        (self.root/"chatgpt-gui-bridge-state/health.json").write_text("{invalid")
        result=observe(self.root,now_utc=NOW)
        self.assertEqual("UNAVAILABLE",result["gui_bridge"]["status"])
        self.assertIn("GUI_BRIDGE_NOT_HEALTHY",result["blockers"])


if __name__=="__main__":
    unittest.main()
