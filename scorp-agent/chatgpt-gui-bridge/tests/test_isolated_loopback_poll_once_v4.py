from __future__ import annotations

from pathlib import Path
import contextlib
import json
import sqlite3
import tempfile
import unittest

from chrome_use_loopback_observer_v4 import LocalChromeReadObservation
from isolated_loopback_poll_once_v4 import run_one_shot_local_poll
from isolated_loopback_poll_journal_v4 import LocalPollJournalReceipt


N = "scorp-r2-isolated-poll-once-fixture"
FOLDER = "r2-loopback-poll-live-unit-fixture"


def good():
    return LocalChromeReadObservation(
        "LOCAL_STATE_OBSERVED_UNATTESTED",
        "LAST_OBSERVED_BROWSER_COUNTS_ONLY_NO_GPT_TERMINAL_PROOF",
        session_count=2, tab_count=1,
        status_endpoint_readable=True, all_three_endpoints_readable=True,
    )


class OneShotLocalPollEntrypointTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name) / "experiments"
        self.root.mkdir()
        self.folder=self.root / FOLDER
        self.folder.mkdir()
        self.exe=self.root.parent / "p0-transport-bakeoff" / "chrome-use" / "bin" / "chrome-use.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"fake exe fixture")

    def run_poll(self, *, observation=None, folder=FOLDER,
                 root=None, exe=None, namespace=N, cadence=15, journal=None):
        return run_one_shot_local_poll(
            experiments_root=root if root is not None else self.root,
            experiment_folder=folder,
            executable=exe if exe is not None else self.exe,
            namespace=namespace,
            cadence_minutes=cadence,
            observer=observation if observation is not None else (lambda **kw: good()),
            journal=journal if journal is not None else __import__(
                "isolated_loopback_poll_journal_v4"
            ).commit_one_nonterminal_local_sample,
        )

    def test_end_to_end_offline_sampler_journal_one_row(self):
        result=self.run_poll()
        self.assertEqual("LOCAL_OBSERVATION_ONLY",result.status)
        self.assertEqual("FIRST_LOCAL_STATE_UNATTESTED",result.journal_category)
        self.assertEqual(2,result.observed_sessions)
        self.assertEqual(1,result.observed_tabs)
        self.assertFalse(result.host_terminal_event_verified)
        self.assertFalse(result.worker_wake_authorized)
        self.assertFalse(result.browser_send_authorized)
        self.assertEqual(0,result.local_model_calls)
        with contextlib.closing(sqlite3.connect(self.folder/"local-poll-journal.sqlite3")) as con:
            self.assertEqual(1,con.execute("SELECT COUNT(*) FROM local_poll_sample").fetchone()[0])

    def test_second_run_blocked_by_actual_persistent_cadence(self):
        self.assertEqual("LOCAL_OBSERVATION_ONLY",self.run_poll().status)
        next_result=self.run_poll()
        self.assertEqual("BLOCKED",next_result.status)
        self.assertIn(
            next_result.reason,
            ("CLOCK_REWIND_OR_DUPLICATE","POLL_CADENCE_NOT_ELAPSED"),
        )
        self.assertFalse(next_result.worker_wake_authorized)

    def test_invokes_only_one_passive_observer(self):
        calls=[]
        def observer(**kwargs):
            calls.append(kwargs)
            return good()
        result=self.run_poll(observation=observer)
        self.assertEqual("LOCAL_OBSERVATION_ONLY",result.status)
        self.assertEqual(1,len(calls))
        self.assertEqual(str(self.exe.resolve()),calls[0]["executable"])
        self.assertEqual(N,calls[0]["pinned_isolated_namespace"])

    def test_original_master_namespace_is_blocked_without_calls(self):
        calls=[]
        def observer(**kwargs):
            calls.append(kwargs)
            return good()
        for name in ("master","original-r1","",None):
            with self.subTest(name=name):
                result=self.run_poll(namespace=name,observation=observer)
                self.assertEqual("ISOLATED_POLL_CLI_ARGUMENT_INVALID",result.reason)
                self.assertFalse(result.worker_wake_authorized)
        self.assertEqual([],calls)

    def test_bad_folder_names_cannot_escape_experiments(self):
        for name in ("../state-v3/active","r2-host-terminal-security-20261009",
                     "/tmp","r2-loopback-poll-", "r2-loopback-poll-a/../bad",
                     "r2-loopback-poll-a\\bad",None):
            with self.subTest(name=name):
                result=self.run_poll(folder=name)
                self.assertEqual("ISOLATED_POLL_CLI_ARGUMENT_INVALID",result.reason)

    def test_other_existing_dir_without_prefix_cannot_be_sampled(self):
        target=self.root/"r2-other-experiment"
        target.mkdir()
        result=self.run_poll(folder=target.name)
        self.assertEqual("ISOLATED_POLL_CLI_ARGUMENT_INVALID",result.reason)
        self.assertFalse((target/"local-poll-journal.sqlite3").exists())

    def test_nonexistent_isolated_folder_not_created(self):
        folder="r2-loopback-poll-never-created"
        result=self.run_poll(folder=folder)
        self.assertEqual("ISOLATED_POLL_CLI_PATH_UNAVAILABLE",result.reason)
        self.assertFalse((self.root/folder).exists())

    def test_non_experiments_root_rejected(self):
        result=self.run_poll(root=self.root.parent)
        self.assertEqual("ISOLATED_POLL_CLI_PATH_UNAVAILABLE",result.reason)

    def test_wrong_chrome_binary_name_refused(self):
        fake=self.exe.parent/"evil-binary.exe"
        fake.write_bytes(b"fake")
        result=self.run_poll(exe=fake)
        self.assertEqual("ISOLATED_POLL_CLI_PATH_INVALID",result.reason)

    def test_missing_chrome_binary_refused(self):
        r=self.run_poll(exe=self.exe.parent/"chrome-use-missing.exe")
        self.assertEqual("ISOLATED_POLL_CLI_PATH_UNAVAILABLE",r.reason)

    def test_unsupported_cadence_cannot_be_requested(self):
        for value in (None,0,1,30,True,False,"15",25.0):
            with self.subTest(value=value):
                self.assertEqual(
                    "ISOLATED_POLL_CLI_ARGUMENT_INVALID",
                    self.run_poll(cadence=value).reason,
                )
        self.assertFalse((self.folder/"local-poll-journal.sqlite3").exists())

    def test_observer_exception_is_redacted_no_journal(self):
        def raises(**kw):
            raise RuntimeError("PRIVATE_COOKIE https://chatgpt.com/c/secret")
        result=self.run_poll(observation=raises)
        self.assertEqual("LOOPBACK_OBSERVATION_UNAVAILABLE",result.reason)
        self.assertNotIn("PRIVATE",repr(result))
        self.assertFalse((self.folder/"local-poll-journal.sqlite3").exists())

    def test_journal_exception_is_redacted(self):
        def boom(*a,**kw):
            raise OSError("PRIVATE_COOKIE")
        result=self.run_poll(journal=boom)
        self.assertEqual("ISOLATED_POLL_JOURNAL_UNAVAILABLE",result.reason)
        self.assertNotIn("PRIVATE",repr(result))

    def test_invalid_source_status_blocks_no_wake(self):
        def blocked(**kwargs):
            return LocalChromeReadObservation("BLOCKED","UNAVAILABLE")
        result=self.run_poll(observation=blocked)
        self.assertEqual("BLOCKED",result.status)
        self.assertEqual("LOCAL_SOURCE_UNVERIFIED_NO_JOURNAL",result.reason)
        self.assertFalse(result.worker_wake_authorized)
        self.assertFalse((self.folder/"local-poll-journal.sqlite3").exists())

    def test_forged_journal_success_still_cannot_authorize_worker(self):
        def returns(*args,**kwargs):
            return LocalPollJournalReceipt(
                status="LOCAL_NONTERMINAL_SAMPLE_COMMITTED",
                reason="OBSERVE_ONLY_NO_GPT_DONE_OR_WAKE_AUTHORITY",
                sample_written=True,
                category="FIRST_LOCAL_STATE_UNATTESTED",
                cadence_minutes=15,
            )
        result=self.run_poll(journal=returns)
        self.assertEqual("LOCAL_OBSERVATION_ONLY",result.status)
        self.assertFalse(result.worker_wake_authorized)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.host_terminal_event_verified)


if __name__=="__main__":
    unittest.main()
