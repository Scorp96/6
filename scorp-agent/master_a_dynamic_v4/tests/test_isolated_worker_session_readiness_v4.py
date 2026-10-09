"""Regression guards: old ChatGPT turn aliases never become live Workers."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from master_a_dynamic_v4.isolated_worker_session_readiness_v4 import (
    inspect_legacy_worker_readiness,
    inspect_legacy_worker_readiness_file,
    main,
)

SECRET_URL = "https://chatgpt.com/c/SHOULD-NEVER-PRINT"
SECRET_SESSION = "private-session-SHOULD-NEVER-PRINT"


def fixture(*, registry=None):
    turns = {}
    for n in range(219):
        turns[f"private-turn-{n}"] = {
            "session": f"session-{n % 4}",
            "conversation_url": SECRET_URL,
            "role": "UNKNOWN",
            "browser_io_started": None,
            "submit_edge_crossed": None,
        }
    c = {
        f"https://chatgpt.com/c/private-{n}": {"session": f"session-{n % 4}"}
        for n in range(6)
    }
    d = {"protocol_version":"scorp.chrome-use-driver/v1", "conversations":c, "turns":turns}
    if registry is not None:
        d["sessions"] = registry
    return d


class LegacyReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "legacy-v3.json"

    def test_historical_six_conversations_219_turns_four_refs_unknown(self):
        v = inspect_legacy_worker_readiness(fixture())
        self.assertEqual("HISTORICAL_METADATA_ONLY", v.status)
        self.assertEqual("LEGACY_SESSION_REGISTRY_MISSING", v.reason)
        self.assertEqual(219, v.turns_total)
        self.assertEqual(6, v.conversations_total)
        self.assertEqual(4, v.distinct_referenced_session_count)
        self.assertEqual(219, v.unknown_browser_io_turns)
        self.assertEqual(219, v.unknown_submit_edge_turns)
        self.assertEqual(219, v.other_or_unknown_role_turns)
        self.assertIsNone(v.registry_rows)
        self.assertFalse(v.genuine_worker_2_session_admitted)
        self.assertFalse(v.browser_send_authorized)

    def test_two_saved_worker_roles_and_sessions_still_cannot_authenticate(self):
        doc = fixture(registry={
            "session-0":{"role":"WORKER","status":"ACTIVE"},
            "session-1":{"role":"WORKER","status":"ACTIVE"},
        })
        for i in (0, 1):
            doc["turns"][f"private-turn-{i}"]["role"]="WORKER"
            doc["turns"][f"private-turn-{i}"]["browser_io_started"]=False
            doc["turns"][f"private-turn-{i}"]["submit_edge_crossed"]=False
        v = inspect_legacy_worker_readiness(doc)
        self.assertEqual(2, v.worker_turns)
        self.assertEqual(2, v.registry_rows)
        self.assertEqual("PERSISTED_SESSIONS_NOT_HOST_ATTESTED", v.reason)
        self.assertFalse(v.two_distinct_gpt_workers_authenticated)
        self.assertFalse(v.original_master_replay_authorized)

    def test_saved_browser_submit_true_remains_no_send(self):
        d=fixture()
        d["turns"]["private-turn-4"]["submit_edge_crossed"]=True
        d["turns"]["private-turn-5"]["browser_io_started"]=True
        v=inspect_legacy_worker_readiness(d)
        self.assertEqual(1,v.explicit_submit_edge_true_turns)
        self.assertEqual(218,v.unknown_submit_edge_turns)
        self.assertFalse(v.browser_send_authorized)

    def test_legacy_missing_flags_never_become_false_by_default(self):
        d=fixture()
        del d["turns"]["private-turn-0"]["browser_io_started"]
        del d["turns"]["private-turn-0"]["submit_edge_crossed"]
        v=inspect_legacy_worker_readiness(d)
        self.assertEqual(219,v.unknown_browser_io_turns)
        self.assertEqual(219,v.unknown_submit_edge_turns)

    def test_bad_protocol_or_synthetic_root_is_blocked(self):
        for d in (None, [], {}, {"turns":{}}, {"protocol_version":"wrong","turns":{},"conversations":{}}):
            self.assertEqual("BLOCKED",inspect_legacy_worker_readiness(d).status)

    def test_wrong_sessions_type_fail_closed(self):
        d=fixture()
        d["sessions"]=["fake-worker-1","fake-worker-2"]
        v=inspect_legacy_worker_readiness(d)
        self.assertEqual("BLOCKED",v.status)
        self.assertEqual("SESSION_REGISTRY_SCHEMA_INVALID",v.reason)

    def test_malformed_turns_and_conversations_fail_closed(self):
        for key in ("turns","conversations"):
            d=fixture()
            d[key]=[]
            self.assertEqual("LEGACY_DICTIONARIES_INVALID",inspect_legacy_worker_readiness(d).reason)

    def test_absent_file_not_created_by_read_only_probe(self):
        result=inspect_legacy_worker_readiness_file(self.path)
        self.assertEqual("UNAVAILABLE",result.status)
        self.assertFalse(self.path.exists())

    def test_malformed_file_fails_without_raw_value_leak(self):
        self.path.write_text("INVALID "+SECRET_URL,encoding="utf-8")
        v=inspect_legacy_worker_readiness_file(self.path)
        self.assertEqual("UNAVAILABLE",v.status)
        self.assertNotIn(SECRET_URL,repr(v))

    def test_file_bytes_do_not_change_after_probe(self):
        self.path.write_text(json.dumps(fixture()),encoding="utf-8")
        digest=hashlib.sha256(self.path.read_bytes()).hexdigest()
        v=inspect_legacy_worker_readiness_file(self.path)
        self.assertEqual(219,v.turns_total)
        self.assertEqual(digest,hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_cli_is_single_sanitized_json_line(self):
        self.path.write_text(json.dumps(fixture()),encoding="utf-8")
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            rc=main(["--state-file",str(self.path)])
        self.assertEqual(0,rc)
        lines=output.getvalue().splitlines()
        self.assertEqual(1,len(lines))
        j=json.loads(lines[0])
        self.assertEqual(219,j["turns_total"])
        self.assertEqual(0,j["worker_turns"])
        self.assertFalse(j["browser_send_authorized"])
        self.assertNotIn(SECRET_URL,lines[0])
        self.assertNotIn(SECRET_SESSION,lines[0])
        self.assertNotIn(str(self.path),lines[0])

    def test_symlinked_state_is_not_followed(self):
        original=Path(self.temp.name)/"real.json"
        original.write_text(json.dumps(fixture()),encoding="utf-8")
        try:
            self.path.symlink_to(original)
        except (OSError,NotImplementedError):
            self.skipTest("symlink unavailable")
        v=inspect_legacy_worker_readiness_file(self.path)
        self.assertEqual("STATE_FILE_MISSING_OR_SYMLINKED",v.reason)

    def test_oversize_state_rejected_before_json_load(self):
        self.path.write_bytes(b"x"*(2*1024*1024+1))
        self.assertEqual("STATE_FILE_OVERSIZE",inspect_legacy_worker_readiness_file(self.path).reason)


if __name__=="__main__":
    unittest.main()
