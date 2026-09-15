from __future__ import annotations

import hashlib
import pathlib
import sqlite3
import tempfile
import unittest


class SqliteMigrationTests(unittest.TestCase):
    def create_legacy_db(self, path: pathlib.Path, *, with_contract: bool = False) -> bytes:
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, schema_sha256 TEXT NOT NULL);
            CREATE TABLE contracts(project_id TEXT PRIMARY KEY, root_contract_json TEXT NOT NULL,
                root_contract_sha256 TEXT NOT NULL, acceptance_contract_json TEXT NOT NULL,
                acceptance_contract_sha256 TEXT NOT NULL, contract_sha256 TEXT NOT NULL,
                imported_snapshot_id TEXT, created_at TEXT NOT NULL);
            CREATE TABLE project_state(project_id TEXT PRIMARY KEY, state_version INTEGER NOT NULL DEFAULT 0,
                master_epoch INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ACTIVE',
                phase TEXT NOT NULL DEFAULT 'BOOTSTRAP', completion_candidate_commit TEXT, updated_at TEXT NOT NULL);
            """
        )
        conn.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00Z', 'legacy')")
        if with_contract:
            conn.execute(
                "INSERT INTO contracts VALUES (?,?,?,?,?,?,?,?)",
                (
                    "legacy-project", '{"objective":"legacy"}', "root-hash",
                    '{"required":["AC1"]}', "acceptance-hash", "contract-hash", None,
                    "2026-01-01T00:00:00Z",
                ),
            )
            conn.execute(
                "INSERT INTO project_state VALUES (?,?,?,?,?,?,?)",
                ("legacy-project", 4, 2, "ACTIVE", "IMPLEMENT", None, "2026-01-01T00:00:00Z"),
            )
        conn.commit()
        conn.close()
        return path.read_bytes()

    def test_migration_is_explicit_and_preserves_source(self):
        from master_a_dynamic_v4.sqlite_migration import migrate_sqlite_snapshot
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "legacy.sqlite3"
            before = self.create_legacy_db(source, with_contract=True)
            destination = root / "migrated.sqlite3"
            receipt = migrate_sqlite_snapshot(source, destination, allowed_roots=[root])
            self.assertEqual("IMPORTED_SNAPSHOT", receipt.status)
            self.assertEqual(before, source.read_bytes())
            self.assertEqual(hashlib.sha256(before).hexdigest(), receipt.source_sha256)
            with StateStore(destination, allowed_roots=[root]) as store:
                contract = store.get_contract("legacy-project")
                self.assertEqual(receipt.snapshot_id, contract["imported_snapshot_id"])
                state = store.get_project_state("legacy-project")
                self.assertEqual(4, state["state_version"])
                with store._connection() as conn:
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM transitions").fetchone()[0])

    def test_missing_contract_blocks_without_claiming_history(self):
        from master_a_dynamic_v4.sqlite_migration import migrate_sqlite_snapshot
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "empty-legacy.sqlite3"
            self.create_legacy_db(source, with_contract=False)
            destination = root / "migrated.sqlite3"
            receipt = migrate_sqlite_snapshot(source, destination, allowed_roots=[root])
            self.assertEqual("BLOCKED", receipt.status)
            self.assertIn("MISSING_ROOT_CONTRACT", receipt.conflicts)
            with StateStore(destination, allowed_roots=[root]) as store:
                with store._connection() as conn:
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM transitions").fetchone()[0])

    def test_existing_destination_is_rejected_and_source_is_read_only(self):
        from master_a_dynamic_v4.sqlite_migration import migrate_sqlite_snapshot
        from master_a_dynamic_v4.state_store import StoreInvariantError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "legacy.sqlite3"
            before = self.create_legacy_db(source)
            destination = root / "already-exists.sqlite3"
            destination.write_bytes(b"sentinel")
            with self.assertRaisesRegex(StoreInvariantError, "MIGRATION_DESTINATION_EXISTS"):
                migrate_sqlite_snapshot(source, destination, allowed_roots=[root])
            self.assertEqual(b"sentinel", destination.read_bytes())
            self.assertEqual(before, source.read_bytes())

    def test_unknown_source_table_blocks_migration_without_copying_data(self):
        from master_a_dynamic_v4.sqlite_migration import migrate_sqlite_snapshot
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "unsupported.sqlite3"
            self.create_legacy_db(source, with_contract=True)
            conn = sqlite3.connect(source)
            try:
                conn.execute("CREATE TABLE vendor_private_state(secret TEXT)")
                conn.execute("INSERT INTO vendor_private_state VALUES ('must-not-copy')")
                conn.commit()
            finally:
                conn.close()
            before = source.read_bytes()
            destination = root / "migrated.sqlite3"
            receipt = migrate_sqlite_snapshot(source, destination, allowed_roots=[root])
            self.assertEqual("BLOCKED", receipt.status)
            self.assertIn("UNSUPPORTED_TABLE:vendor_private_state", receipt.conflicts)
            self.assertEqual(before, source.read_bytes())
            with StateStore(destination, allowed_roots=[root]) as store:
                with store._connection() as conn:
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
