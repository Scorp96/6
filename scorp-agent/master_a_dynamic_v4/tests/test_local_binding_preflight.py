from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sqlite3
import subprocess
import tempfile
import unittest

from master_a_dynamic_v4.local_binding_preflight import inspect_local_binding, main

URL="https://chatgpt.com/c/old-master"
OTHER="https://chatgpt.com/c/new-unregistered"


class FakeCli:
    def __init__(self, *, chat_url=URL, ownership="adopted"):
        self.chat_url=chat_url
        self.ownership=ownership
        self.calls=[]
    def __call__(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if argv[1:]==["--json","session","list"]:
            body={"sessions":["owned-s"]}
        elif argv[1:]==["--session","owned-s","--json","get","url"]:
            body={"data":{"url":self.chat_url}}
        elif argv[1:]==["--session","owned-s","--json","tab","list"]:
            body={"success":True,"data":{"tabs":[{"url":self.chat_url,"ownership":self.ownership}]}}
        else:
            raise AssertionError("UNRECOGNIZED_BROWSER_MUTATION")
        return subprocess.CompletedProcess(argv,0,json.dumps(body),"")


class LocalBindingPreflightTests(unittest.TestCase):
    def _fixture(self, root, *, legacy_states=(), health_status="IDLE", error=None):
        v3=root/"v3";v3.mkdir()
        (v3/"project-state.json").write_text(
            json.dumps({"project_id":"private-project","status":"ACTIVE"}),encoding="utf-8"
        )
        (v3/"sessions-v3.json").write_text(
            json.dumps({"master-conversation:private-project":{"conversation_url":URL}}),
            encoding="utf-8",
        )
        health=root/"health.json"
        health.write_text(
            json.dumps({"status":health_status,"error":error}),encoding="utf-8"
        )
        db=root/"state.sqlite3"
        with contextlib.closing(sqlite3.connect(db)) as con, con:
            con.execute("CREATE TABLE project_state(status TEXT)")
            con.execute("INSERT INTO project_state VALUES('ACTIVE')")
            con.execute("CREATE TABLE operator_controls(operator_state TEXT)")
            con.execute("INSERT INTO operator_controls VALUES('RUNNING')")
            con.execute("CREATE TABLE action_intents(state TEXT)")
            con.executemany(
                "INSERT INTO action_intents VALUES(?)",
                [(state,) for state in legacy_states],
            )
        return v3,health,db

    def _inspect(self, v3, health, db, fake):
        return inspect_local_binding(
            v3_project_root=v3,gui_health_file=health,r1_state_db=db,
            chrome_use_executable=r"C:\Tools\chrome-use.exe",run=fake,
        )

    def test_known_rotation_conflict_blocks_matching_url(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t),health_status="ERROR",
                                  error="ValueError: MASTER_CONVERSATION_ROTATION_REQUIRED")
            cli=FakeCli()
            result=self._inspect(v3,h,db,cli)
            self.assertEqual("BLOCKED",result.status)
            self.assertEqual("MASTER_ROTATION_CONFLICT_UNRESOLVED",result.reason)
            self.assertTrue(result.rotation_conflict)
            self.assertFalse(result.browser_send_authorized)

    def test_pending_remote_submissions_are_counted_as_unresolved(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t),
                                  legacy_states=("BLOCKED_AMBIGUOUS","MAY_HAVE_SUBMITTED","CONFIRMED_SUBMITTED"))
            cli=FakeCli()
            result=self._inspect(v3,h,db,cli)
            self.assertEqual(3,result.unresolved_intents)
            self.assertEqual("LEGACY_MASTER_INTENT_UNRESOLVED",result.reason)

    def test_no_rotation_no_pending_still_requires_review_not_send(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t))
            result=self._inspect(v3,h,db,FakeCli())
            self.assertEqual("READONLY_MATCH_REVIEW_REQUIRED",result.status)
            self.assertFalse(result.browser_send_authorized)
            self.assertFalse(result.browser_adoption_authorized)

    def test_different_live_tab_does_not_become_master(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t))
            result=self._inspect(v3,h,db,FakeCli(chat_url=OTHER))
            self.assertEqual("CANONICAL_MASTER_TAB_NOT_FOUND",result.reason)

    def test_unknown_gui_health_blocks_before_any_browser_probe(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t),health_status="UNKNOWN")
            fake=FakeCli()
            result=self._inspect(v3,h,db,fake)
            self.assertEqual("GUI_HEALTH_STATE_UNVERIFIED",result.reason)
            self.assertEqual([],fake.calls)

    def test_r1_database_missing_blocks_without_create(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t))
            db.unlink()
            fake=FakeCli()
            result=self._inspect(v3,h,db,fake)
            self.assertEqual("R1_SQLITE_MISSING",result.reason)
            self.assertEqual([],fake.calls)
            self.assertFalse(db.exists())

    def test_sqlite_missing_table_does_not_fallback_to_zero(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t))
            with contextlib.closing(sqlite3.connect(db)) as c,c:
                c.execute("DROP TABLE action_intents")
            fake=FakeCli()
            result=self._inspect(v3,h,db,fake)
            self.assertEqual("LOCAL_AUTHORITY_READ_FAILED",result.reason)
            self.assertEqual([],fake.calls)

    def test_foreign_tab_cannot_claim_master(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t))
            result=self._inspect(v3,h,db,FakeCli(ownership="foreign"))
            self.assertEqual("MASTER_TAB_NOT_OWNED_BY_SESSION",result.reason)

    def test_cli_json_never_contains_canonical_url_or_user_data(self):
        with tempfile.TemporaryDirectory() as t:
            v3,h,db=self._fixture(pathlib.Path(t))
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                rc=main([
                    "--v3-project-root",str(v3),
                    "--gui-health-file",str(h),
                    "--r1-state-db",str(db),
                    "--chrome-use-executable", "",
                ])
            self.assertEqual(0,rc)
            self.assertNotIn(URL,out.getvalue())
            self.assertNotIn("private-project",out.getvalue())
            self.assertEqual("CLI_EXECUTABLE_MISSING",json.loads(out.getvalue())["reason"])


if __name__ == "__main__":
    unittest.main()
