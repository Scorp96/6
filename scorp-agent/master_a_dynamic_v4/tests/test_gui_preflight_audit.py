from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
import unittest

from master_a_dynamic_v4.gui_preflight_audit import inspect_gui_preflight

URL="https://chatgpt.com/c/secret-not-to-output"
ERROR="MASTER_CONVERSATION_ROTATION_REQUIRED"

class GuiPreflightTests(unittest.TestCase):
    def _fixture(self,root):
        v3=root/"v3";v3.mkdir()
        health=root/"health.json"
        db=root/"state.sqlite3"
        (v3/"project-state.json").write_text(
            json.dumps({"project_id":"project-private","status":"ACTIVE"}),
            encoding="utf-8",
        )
        (v3/"sessions-v3.json").write_text(json.dumps({
            "master-conversation:project-private":{"conversation_url":URL},
            "session:1":{"project_id":"project-private","actor_kind":"MASTER",
                         "session_id":"secret-id","conversation_url":URL},
        }),encoding="utf-8")
        (v3/"master-worker-v3-ledger.json").write_text(json.dumps({
            "secret-turn-1":{"state":"GUI_AMBIGUOUS","actor_kind":"MASTER"},
            "secret-turn-2":{"state":"GUI_SUBMITTED","actor_kind":"WORKER",
                             "conversation_url":URL},
        }),encoding="utf-8")
        health.write_text(json.dumps({"status":"ERROR","error":ERROR}),encoding="utf-8")
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE action_intents(state TEXT)")
            c.executemany("INSERT INTO action_intents(state) VALUES(?)",
                          [("BLOCKED_AMBIGUOUS",),("RESPONSE_CAPTURED",)])
        return v3,health,db

    def test_report_is_sanitized_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            v3,health,db=self._fixture(pathlib.Path(tmp))
            report=inspect_gui_preflight(v3,health,db)
            self.assertEqual("BLOCKED_ROTATION_RECONCILIATION",report["next_action"])
            self.assertFalse(report["browser_send_authorized"])
            self.assertEqual(1,report["r1_ambiguous_intent_count"])
            self.assertEqual(1,report["v3_ambiguous_without_url"])
            self.assertEqual({"MASTER":1},report["v3_ambiguous_actors"])
            serialized=json.dumps(report,ensure_ascii=False)
            for private in (URL,"secret-id","secret-turn-1","project-private",ERROR):
                self.assertNotIn(private,serialized)

    def test_matching_master_binding_does_not_resolve_new_rotation_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            v3,health,db=self._fixture(pathlib.Path(tmp))
            report=inspect_gui_preflight(v3,health,db)
            self.assertTrue(report["master_sessions_consistent"])
            self.assertTrue(report["bridge_rotation_blocked"])

    def test_without_rotation_original_ambiguous_intent_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            v3,health,db=self._fixture(pathlib.Path(tmp))
            health.write_text(json.dumps({"status":"IDLE"}),encoding="utf-8")
            report=inspect_gui_preflight(v3,health,db)
            self.assertEqual("BLOCKED_AMBIGUOUS_INTENT",report["next_action"])
            self.assertEqual("READ_ONLY",report["r1_sqlite_access"])

    def test_missing_sqlite_does_not_create_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            v3,health,db=self._fixture(pathlib.Path(tmp))
            db.unlink()
            report=inspect_gui_preflight(v3,health,db)
            self.assertEqual("NOT_FOUND",report["r1_sqlite_access"])
            self.assertFalse(db.exists())

    def test_missing_registry_cannot_implicitly_authorize_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            v3,health,db=self._fixture(pathlib.Path(tmp))
            (v3/"sessions-v3.json").unlink()
            health.write_text(json.dumps({"status":"IDLE"}),encoding="utf-8")
            with sqlite3.connect(db) as c:
                c.execute("DELETE FROM action_intents")
            report=inspect_gui_preflight(v3,health,db)
            self.assertEqual("REQUIRES_PHYSICAL_BINDING_VERIFICATION",report["next_action"])
            self.assertFalse(report["browser_send_authorized"])


if __name__=="__main__":
    unittest.main()
