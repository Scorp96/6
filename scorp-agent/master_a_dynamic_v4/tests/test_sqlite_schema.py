from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import unittest


REQUIRED_TABLES = {
    "daemon_leases",
    "daemon_supervision",
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
    "operator_controls",
    "runtime_observations",
    "runtime_command_receipts",
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
                with store._connection() as conn:
                    assignment_columns = {row[1] for row in conn.execute("PRAGMA table_info(assignments)")}
                    lease_columns = {row[1] for row in conn.execute("PRAGMA table_info(leases)")}
                self.assertIn("base_state_version", assignment_columns)
                self.assertIn("heartbeat_at", lease_columns)
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

    def test_schema_v1_database_is_migrated_to_v6_for_runtime_command_core(self):
        StateStore, _ = self.load_api()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = root / "scorp-v4.sqlite3"
            StateStore(db, allowed_roots=[root]).close()
            conn = sqlite3.connect(db)
            try:
                conn.execute("UPDATE schema_migrations SET version=1")
                conn.commit()
            finally:
                conn.close()
            with StateStore(db, allowed_roots=[root]) as migrated:
                with migrated._connection() as conn:
                    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(daemon_leases)").fetchall()}
                self.assertEqual([1, 2, 3, 4, 5, 6], versions)
                self.assertIn("daemon_leases", migrated.table_names())
                self.assertIn("runtime_command_receipts", migrated.table_names())
                self.assertIn("lease_status", columns)

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

    def test_non_v4_existing_sqlite_requires_explicit_migration(self):
        StateStore, StoreInvariantError = self.load_api()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = root / "legacy.sqlite3"
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE legacy_state (project_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                conn.execute("INSERT INTO legacy_state(project_id,payload) VALUES('p1','legacy')")
                conn.execute("PRAGMA user_version=0")
                conn.commit()
            finally:
                conn.close()

            with self.assertRaisesRegex(StoreInvariantError, "EXPLICIT_MIGRATION_REQUIRED"):
                StateStore(db, allowed_roots=[root])

            # The guard is read-only: it must not add V4 tables or alter the
            # legacy source before a named migration operation is selected.
            conn = sqlite3.connect(db)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                row = conn.execute("SELECT payload FROM legacy_state WHERE project_id='p1'").fetchone()
            finally:
                conn.close()
            self.assertEqual({"legacy_state"}, tables)
            self.assertEqual(("legacy",), row)


if __name__ == "__main__":
    unittest.main()
