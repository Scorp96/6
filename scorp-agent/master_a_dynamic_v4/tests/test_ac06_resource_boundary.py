from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import tempfile
import unittest


class ResourceBoundaryTests(unittest.TestCase):
    def test_path_policy_rejects_escape_prefix_trick_relative_and_protected_paths(self):
        from master_a_dynamic_v4.path_policy import PathBoundaryError, PathPolicy

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            allowed = root / "worktree"
            allowed.mkdir()
            sibling = root / "worktree-evil"
            sibling.mkdir()
            policy = PathPolicy([allowed])
            accepted = policy.authorize([allowed / "nested" / "result.json"], "write")
            self.assertEqual((str((allowed / "nested" / "result.json").resolve()),), accepted)
            for bad in (sibling / "result.json", allowed / ".." / "outside.txt", pathlib.Path("relative.txt")):
                with self.subTest(path=bad), self.assertRaises(PathBoundaryError):
                    policy.authorize([bad], "write")
            with self.assertRaises(PathBoundaryError):
                PathPolicy([allowed], protected_roots=[allowed / "protected"]).authorize(
                    [allowed / "protected" / "state.json"], "write"
                )

    def test_symlink_or_reparse_escape_is_rejected_when_supported(self):
        from master_a_dynamic_v4.path_policy import PathBoundaryError, PathPolicy

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            link = allowed / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(0, created.returncode, created.stdout.decode(errors="replace"))
            with self.assertRaises(PathBoundaryError):
                PathPolicy([allowed]).authorize([link / "escaped.txt"], "write")

    def test_conflicting_write_scope_is_serialized(self):
        from master_a_dynamic_v4.path_policy import PathPolicy
        from master_a_dynamic_v4.scheduler import Scheduler
        from master_a_dynamic_v4.state_store import StateStore

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            worktree = root / "worktree"
            worktree.mkdir()
            store = StateStore(root / "state.sqlite3", allowed_roots=[worktree])
            store.create_contract("project-ac06", root_contract={"objective": "scope"}, acceptance_contract={"required": ["AC06"]})
            scheduler = Scheduler(store, "project-ac06", PathPolicy([worktree]), max_workers=2)
            scheduler.enqueue_graph([
                {"task_id": "T1", "objective_sha256": "1" * 64, "resource_scope": [worktree / "shared.txt"], "access_mode": "write", "dependencies": []},
                {"task_id": "T2", "objective_sha256": "2" * 64, "resource_scope": [worktree / "shared.txt"], "access_mode": "write", "dependencies": []},
            ])
            try:
                claims = scheduler.claim_runnable(master_epoch=0, now=dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc))
                self.assertEqual(["T1"], [claim.task_id for claim in claims])
                self.assertEqual("QUEUED", scheduler.get_task("T2")["state"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
