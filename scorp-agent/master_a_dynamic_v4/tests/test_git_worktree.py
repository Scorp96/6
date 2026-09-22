from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Claim:
    assignment_id: str
    task_id: str
    access_mode: str
    resource_scope: tuple[str, ...]
    master_epoch: int = 1
    lease_token: str = "lease-git"
    task_context: dict = field(default_factory=dict)


class GitWorktreeTests(unittest.TestCase):
    def make_repo(self, root: pathlib.Path) -> tuple[pathlib.Path, str]:
        repo = root / "repo"
        repo.mkdir()
        self.run_git(["init", "--initial-branch=main", str(repo)], root)
        self.run_git(["-C", str(repo), "config", "user.email", "test@example.invalid"], root)
        self.run_git(["-C", str(repo), "config", "user.name", "SCORP Test"], root)
        (repo / "README.md").write_text("baseline\n", encoding="utf-8")
        self.run_git(["-C", str(repo), "add", "README.md"], root)
        self.run_git(["-C", str(repo), "commit", "-m", "baseline"], root)
        commit = self.run_git(["-C", str(repo), "rev-parse", "HEAD"], root).stdout.strip()
        return repo, commit

    @staticmethod
    def run_git(args, cwd):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )

    def test_prepares_detached_worktree_at_verified_base(self):
        from master_a_dynamic_v4.git_worktree import GitWorktreeManager

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            repo, commit = self.make_repo(root)
            target = root / "worker-worktree"
            claim = Claim("assignment-git", "T1", "write", (str(target),), task_context={"repository_root": str(repo)})
            receipt = GitWorktreeManager([root]).prepare(
                claim, repository=repo, worktree=target, base_commit=commit
            )
            self.assertEqual(commit, receipt.base_commit)
            self.assertEqual(commit, receipt.head_commit)
            observed = self.run_git(["-C", str(target), "rev-parse", "HEAD"], root).stdout.strip()
            self.assertEqual(commit, observed)
            self.assertTrue((target / "README.md").is_file())

    def test_verifies_durable_prepared_worktree_without_creating_another(self):
        from master_a_dynamic_v4.git_worktree import GitWorktreeManager

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            repo, commit = self.make_repo(root)
            target = root / "worker-worktree"
            claim = Claim(
                "assignment-git-recovery",
                "T1",
                "write",
                (str(target),),
                task_context={"repository_root": str(repo)},
            )
            manager = GitWorktreeManager([root])
            receipt = manager.prepare(
                claim, repository=repo, worktree=target, base_commit=commit
            )
            verified = manager.verify_prepared(
                claim,
                receipt=receipt.as_dict(),
                repository=repo,
                worktree=target,
                base_commit=commit,
            )
            self.assertEqual(commit, verified.head_commit)
            self.assertEqual(target.resolve(), pathlib.Path(verified.worktree))

    def test_rejects_dirty_repository_before_creating_worktree(self):
        from master_a_dynamic_v4.git_worktree import GitWorktreeManager, GitWorktreeRejected

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            repo, commit = self.make_repo(root)
            (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
            target = root / "worker-worktree"
            claim = Claim("assignment-git", "T1", "write", (str(target),), task_context={"repository_root": str(repo)})
            with self.assertRaisesRegex(GitWorktreeRejected, "REPOSITORY_DIRTY"):
                GitWorktreeManager([root]).prepare(
                    claim, repository=repo, worktree=target, base_commit=commit
                )
            self.assertFalse(target.exists())

    def test_rejects_existing_target_and_scope_escape(self):
        from master_a_dynamic_v4.git_worktree import GitWorktreeManager, GitWorktreeRejected

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            repo, commit = self.make_repo(root)
            target = root / "worker-worktree"
            target.mkdir()
            (target / "existing.txt").write_text("occupied\n", encoding="utf-8")
            claim = Claim("assignment-git", "T1", "write", (str(target),), task_context={"repository_root": str(repo)})
            with self.assertRaisesRegex(GitWorktreeRejected, "WORKTREE_TARGET_NOT_EMPTY"):
                GitWorktreeManager([root]).prepare(
                    claim, repository=repo, worktree=target, base_commit=commit
                )
            outside = root / "outside-worker-worktree"
            empty_claim = Claim("assignment-git", "T1", "write", (str(root / "different"),), task_context={"repository_root": str(repo)})
            with self.assertRaisesRegex(GitWorktreeRejected, "WORKTREE_OUTSIDE_ASSIGNMENT_SCOPE"):
                GitWorktreeManager([root]).prepare(
                    empty_claim, repository=repo, worktree=outside, base_commit=commit
                )

    def test_rejects_source_repository_different_from_assignment_binding(self):
        from master_a_dynamic_v4.git_worktree import GitWorktreeManager, GitWorktreeRejected

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            authorized_parent = root / "authorized-root"
            authorized_parent.mkdir()
            authorized_repo, commit = self.make_repo(authorized_parent)
            other_root = root / "other-root"
            other_root.mkdir()
            requested_repo, requested_commit = self.make_repo(other_root)
            target = root / "worker-worktree"
            claim = Claim(
                "assignment-git-bound",
                "T1",
                "write",
                (str(target),),
                task_context={"repository_root": str(authorized_repo)},
            )
            with self.assertRaisesRegex(GitWorktreeRejected, "REPOSITORY_BINDING_MISMATCH"):
                GitWorktreeManager([root]).prepare(
                    claim,
                    repository=requested_repo,
                    worktree=target,
                    base_commit=requested_commit,
                )


if __name__ == "__main__":
    unittest.main()
