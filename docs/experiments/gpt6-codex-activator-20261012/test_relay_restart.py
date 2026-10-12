"""Isolated cross-process Codex activation/recording gate tests.

Each CLI action runs a NEW Python interpreter. No browser, network, GPT or
production ScorpAgent database is accessed. All urls are fictitious fixtures.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
RELAY = HERE / "relay.py"
URL = "https://chatgpt.com/c/isolated-restart-canary-001"
PROMPT_SHA = hashlib.sha256(b"GPT_EXECUTES_CODEX_ONLY_ACTIVATES").hexdigest()


class RestartAndEpochAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "canary.sqlite3"
        self.request1 = "stage2-request-001"
        self.request2 = "stage2-request-002"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *params, rc=0):
        command = [sys.executable, "-B", str(RELAY), "--db", str(self.db), *params]
        p = subprocess.run(command, cwd=str(HERE), capture_output=True, text=True,
                           encoding="utf-8", timeout=15)
        self.assertEqual(p.returncode, rc, msg=p.stderr + "\n" + p.stdout)
        return json.loads(p.stdout)

    def prepare(self, rid=None, epoch=1, turn=1, rc=0):
        return self.run_cli(
            "prepare", "--request-id", rid or self.request1,
            "--project-id", "stage2-fixture",
            "--conversation-url", URL,
            "--prompt-sha256", PROMPT_SHA,
            "--epoch", str(epoch), "--turn", str(turn), rc=rc)

    def claim(self, rid=None, who="local-codex", rc=0):
        return self.run_cli(
            "claim", "--request-id", rid or self.request1,
            "--activator-id", who, rc=rc)

    def arm(self, rid=None, who="local-codex", rc=0):
        return self.run_cli(
            "arm", "--request-id", rid or self.request1,
            "--activator-id", who, rc=rc)

    def inspect(self, rid=None):
        return self.run_cli("inspect", "--request-id", rid or self.request1)

    def test_restart_prepared_to_claimed(self):
        self.prepare()
        self.assertEqual(self.inspect()["result"]["state"], "PREPARED")
        self.claim()
        self.assertEqual(self.inspect()["result"]["state"], "CLAIMED")

    def test_restart_after_arm_forbids_second_submission(self):
        self.prepare()
        self.claim()
        self.arm()
        self.assertEqual(self.inspect()["result"]["state"], "MAY_HAVE_SUBMITTED")
        blocked = self.claim(rc=2)
        self.assertEqual(blocked["reason"], "ACTIVATION_UNCERTAIN_NO_RETRY")
        self.assertFalse(self.inspect()["result"]["safe_to_retry_send"])
        self.assertTrue(self.run_cli(
            "verify", "--request-id", self.request1
        )["result"]["chain_valid"])

    def test_two_turn_fixture_no_real_gpt(self):
        self.prepare()
        self.claim()
        self.arm()
        self.prepare(self.request2, epoch=1, turn=2)
        self.claim(self.request2)
        self.arm(self.request2)
        self.assertEqual(self.inspect(self.request1)["result"]["state"],
                         "MAY_HAVE_SUBMITTED")
        self.assertEqual(self.inspect(self.request2)["result"]["state"],
                         "MAY_HAVE_SUBMITTED")
        for rid in (self.request1, self.request2):
            self.assertEqual(self.run_cli("verify", "--request-id", rid)["result"]["event_count"], 3)

    def test_restarted_observation_remains_unverified(self):
        self.prepare()
        self.claim()
        self.arm()
        result = self.run_cli(
            "observe", "--request-id", self.request1, "--activator-id",
            "local-codex", "--observed-url", URL, "--response-sha256",
            "a" * 64, "--observation-ref", "fixture-no-remote-proof"
        )
        self.assertEqual(result["result"]["state"], "OBSERVED_UNVERIFIED")
        self.assertEqual(self.inspect()["result"]["next_action"], "GPT_REVIEW_EXTERNAL_EVIDENCE")
        blocked = self.claim(rc=2)
        self.assertEqual(blocked["reason"], "ACTIVATION_UNCERTAIN_NO_RETRY")

    def test_new_epoch_fences_old_activator(self):
        self.prepare()
        self.claim()
        self.prepare(self.request2, epoch=2, turn=1)
        blocked = self.arm(rc=2)
        self.assertEqual(blocked["reason"], "MASTER_EPOCH_STALE")
        blocked2 = self.claim(rc=2)
        self.assertEqual(blocked2["reason"], "MASTER_EPOCH_STALE")
        self.claim(self.request2)
        self.assertEqual(self.inspect(self.request2)["result"]["master_epoch"], 2)

    def test_stale_epoch_new_work_rejected(self):
        self.prepare(self.request1, epoch=2, turn=1)
        old = self.prepare(self.request2, epoch=1, turn=2, rc=2)
        self.assertEqual(old["reason"], "MASTER_EPOCH_STALE")

    def test_deleted_final_journal_event_detected(self):
        self.prepare()
        self.claim()
        self.arm()
        conn = sqlite3.connect(self.db)
        conn.execute(
            "DELETE FROM journal WHERE request_id=? AND event_name='MAY_HAVE_SUBMITTED'",
            (self.request1,))
        conn.commit()
        conn.close()
        result = self.run_cli("verify", "--request-id", self.request1, rc=2)
        self.assertEqual(result["reason"], "JOURNAL_STATE_MISMATCH")

    def test_journal_event_name_tamper_detected(self):
        self.prepare()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE journal SET event_name='HIDDEN' WHERE request_id=?", (self.request1,))
        conn.commit()
        conn.close()
        result = self.run_cli("verify", "--request-id", self.request1, rc=2)
        self.assertTrue(result["reason"].startswith("JOURNAL_HASH_CHAIN_BROKEN"))

    def test_wrong_actor_after_restart_rejected(self):
        self.prepare()
        self.claim()
        blocked = self.arm(who="other-codex", rc=2)
        self.assertEqual(blocked["reason"], "CLAIM_BINDING_INVALID")
        self.assertEqual(self.inspect()["result"]["state"], "CLAIMED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
