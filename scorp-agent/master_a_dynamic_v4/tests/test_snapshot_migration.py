from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest


class SnapshotMigrationTests(unittest.TestCase):
    def write_json(self, path: pathlib.Path, value: dict) -> bytes:
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        path.write_bytes(raw)
        return raw

    def test_valid_snapshot_is_hashed_imported_once_and_exported_read_only_without_history(self):
        from master_a_dynamic_v4.export_snapshot import export_read_only
        from master_a_dynamic_v4.import_snapshot import import_snapshot
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "legacy-project-state.json"
            original = self.write_json(source, {
                "protocol_version": "scorp.project-state/v1",
                "project_id": "legacy-project",
                "root_contract": {"objective": "preserve legacy state as evidence"},
                "acceptance_contract": {"required": ["AC-MIGRATION"]},
                "state_version": 9,
                "status": "ACTIVE",
                "current_phase": "IMPLEMENT",
                "completed": ["historical-item-without-v4-proof"],
            })
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            try:
                receipt = import_snapshot(source, store)
                self.assertEqual("IMPORTED", receipt.status)
                self.assertEqual(hashlib.sha256(original).hexdigest(), receipt.source_sha256)
                self.assertEqual(original, source.read_bytes())
                contract = store.get_contract("legacy-project")
                self.assertEqual(receipt.snapshot_id, contract["imported_snapshot_id"])
                exported = export_read_only(store, "legacy-project")
                self.assertEqual("READ_ONLY_EXPORT", exported["authority"])
                self.assertEqual(receipt.snapshot_id, exported["contract"]["imported_snapshot_id"])
                self.assertEqual([], exported["transitions"])
                self.assertEqual([], exported["events"])
                self.assertEqual(0, exported["project_state"]["state_version"])
                replay = import_snapshot(source, store)
                self.assertEqual(receipt, replay)
            finally:
                store.close()

    def test_missing_root_contract_is_blocked_and_source_remains_immutable(self):
        from master_a_dynamic_v4.import_snapshot import import_snapshot
        from master_a_dynamic_v4.state_store import StateStore, StoreInvariantError

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "missing-root.json"
            original = self.write_json(source, {
                "project_id": "missing-root",
                "acceptance_contract": {"required": ["AC-MIGRATION"]},
            })
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            try:
                receipt = import_snapshot(source, store)
                self.assertEqual("BLOCKED", receipt.status)
                self.assertIn("MISSING_ROOT_CONTRACT", receipt.conflicts)
                self.assertEqual(original, source.read_bytes())
                with self.assertRaises(StoreInvariantError):
                    store.get_contract("missing-root")
            finally:
                store.close()

    def test_existing_identity_conflict_is_recorded_not_overwritten(self):
        from master_a_dynamic_v4.import_snapshot import import_snapshot
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "conflict.json"
            original = self.write_json(source, {
                "project_id": "same-project",
                "root_contract": {"objective": "new conflicting objective"},
                "acceptance_contract": {"required": ["AC-NEW"]},
            })
            store = StateStore(root / "state.sqlite3", allowed_roots=[root])
            try:
                before = store.create_contract(
                    "same-project",
                    root_contract={"objective": "authoritative V4 objective"},
                    acceptance_contract={"required": ["AC-V4"]},
                )
                receipt = import_snapshot(source, store)
                after = store.get_contract("same-project")
                self.assertEqual("BLOCKED", receipt.status)
                self.assertIn("CONTRACT_IDENTITY_CONFLICT", receipt.conflicts)
                self.assertEqual(before["contract_sha256"], after["contract_sha256"])
                self.assertEqual(original, source.read_bytes())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
