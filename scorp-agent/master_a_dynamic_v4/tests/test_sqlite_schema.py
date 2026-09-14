from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import unittest


REQUIRED_TABLES = {
    "contracts",
    "project_state",
    "master_sessions",
    "task_nodes",
    "task_dependencies",
    "assignments",
    "leases",
    "events",
    "transitions",
    "action_intents",
    "outbox",
    "browser_bindings",
    "candidate_results",
    "evidence_receipts",
    "review_deadlines",
    "review_findings",
    "release_candidates",
    "imported_snapshots",
    "schema_migrations",
}


class SqliteSchemaTests(unittest.TestCase):
    def load_api(self):
        from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError

        return StateStore, StoreInvariantError

    def test_new_database_uses_exact_durability_pragmas_and_required_tables(self):
        StateStore, _ = self.load_api()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = root / "state" / "scorp-v4.sqlite3"
            with StateStore(db, allowed_roots=[root]) as store:
                self.assertEqual(
                    {
                        "journal_mode": "delete",
                        "synchronous": 2,
                        "foreign_keys": 1,
                        "busy_timeout": 5000,
                    },
                    store.connection_settings(),
                )
                self.assertEqual(REQUIRED_TABLES, set(store.table_names()))
            self.assertTrue(db.is_file())

    def test_reopen_preserves_schema_and_checks_every_new_connection(self):
        StateStore, _ = self.load_api()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = root / "scorp-v4.sqlite3"
            StateStore(db, allowed_roots=[root]).close()
            with StateStore(db, allowed_roots=[root]) as reopened:
                first = reopened.connection_settings()
                second = reopened.connection_settings()
                self.assertEqual(first, second)
                self.assertEqual("delete", second["journal_mode"])
                self.assertEqual(5000, second["busy_timeout"])

    def test_unsupported_schema_version_fails_closed(self):
        StateStore, StoreInvariantError = self.load_api()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = root / "scorp-v4.sqlite3"
            StateStore(db, allowed_roots=[root]).close()
            conn = sqlite3.connect(db)
            try:
                conn.execute("UPDATE schema_migrations SET version=999")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(StoreInvariantError, "SCHEMA_VERSION_UNSUPPORTED"):
                StateStore(db, allowed_roots=[root])


if __name__ == "__main__":
    unittest.main()
