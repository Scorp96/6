"""Regression guards: old ChatGPT turn aliases never become live Workers."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from types import SimpleNamespace
from unittest import mock

from master_a_dynamic_v4.isolated_worker_session_readiness_v4 import (
    inspect_legacy_worker_readiness,
    inspect_legacy_worker_readiness_file,
    main,
    inspect_chrome_use_session_inventory,
    BrowserSessionInventory,
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

    def test_live_cli_count_one_is_not_a_worker_identity(self):
        self.path.write_text("fake binary",encoding="utf-8")
        runner=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps({
            "sessions":["PRIVATE_SESSION_DO_NOT_PRINT"]
        }))
        value=inspect_chrome_use_session_inventory(self.path,runner=runner)
        self.assertEqual("OBSERVED_SESSION_OBJECTS_ONLY",value.status)
        self.assertEqual(1,value.observed_session_objects)
        self.assertFalse(value.distinct_gpt_workers_host_authenticated)
        self.assertFalse(value.browser_send_authorized)
        self.assertNotIn("PRIVATE_SESSION_DO_NOT_PRINT",repr(value))

    def test_live_two_named_cli_session_objects_are_still_not_gpt_authentication(self):
        self.path.write_bytes(b"x")
        r=inspect_chrome_use_session_inventory(self.path,runner=lambda *a,**kw:
            SimpleNamespace(returncode=0,stdout=json.dumps({
                "sessions":{"SECRET_A":{},"SECRET_B":{}}
            })))
        self.assertEqual(2,r.observed_session_objects)
        self.assertFalse(r.distinct_gpt_workers_host_authenticated)

    def test_live_missing_sessions_field_must_not_be_counted_as_one(self):
        self.path.write_bytes(b"x")
        for fake in ({"success":True}, {"sessions":None}, {"data":{"status":"ok"}}):
            v=inspect_chrome_use_session_inventory(self.path,runner=lambda *a,**kw:
                SimpleNamespace(returncode=0,stdout=json.dumps(fake)))
            self.assertIsNone(v.observed_session_objects)
            self.assertEqual("SESSION_COUNT_UNPROVEN",v.reason)

    def test_live_nested_data_sessions_is_accepted_as_count_only(self):
        self.path.write_bytes(b"x")
        result=inspect_chrome_use_session_inventory(self.path,runner=lambda *a,**kw:
            SimpleNamespace(returncode=0,stdout=json.dumps({"data":{"sessions":[1,2,3]}})))
        self.assertEqual(3,result.observed_session_objects)
        self.assertFalse(result.distinct_gpt_workers_host_authenticated)

    def test_live_cli_failure_or_malformed_json_is_unavailable(self):
        self.path.write_bytes(b"x")
        for result in (SimpleNamespace(returncode=2,stdout="private"),
                       SimpleNamespace(returncode=0,stdout="{BROKEN"),
                       SimpleNamespace(returncode=0,stdout='{"success":false,"sessions":[1]}')):
            value=inspect_chrome_use_session_inventory(self.path,runner=lambda *a,**kw:result)
            self.assertEqual("UNAVAILABLE",value.status)
            self.assertIsNone(value.observed_session_objects)

    def test_live_cli_timeout_is_bounded_and_fail_closed(self):
        self.path.write_bytes(b"x")
        def timeout(*a,**kw):
            self.assertLessEqual(kw["timeout"],20)
            raise subprocess.TimeoutExpired("chrome-use",kw["timeout"])
        v=inspect_chrome_use_session_inventory(self.path,runner=timeout)
        self.assertEqual("SESSION_LIST_UNAVAILABLE",v.reason)
        self.assertFalse(v.browser_send_authorized)

    def test_live_absent_executable_does_not_launch_any_runner(self):
        v=inspect_chrome_use_session_inventory(self.path,runner=lambda *a,**kw:self.fail("called"))
        self.assertEqual("CLI_BINARY_MISSING_OR_SYMLINKED",v.reason)

    def test_live_cli_timeout_validation_and_no_network_side_effect(self):
        self.path.write_bytes(b"x")
        v=inspect_chrome_use_session_inventory(self.path,timeout_seconds=0,
            runner=lambda *a,**kw:self.fail("called"))
        self.assertEqual("BOUND_TIMEOUT_INVALID",v.reason)

    def test_cli_with_live_count_remains_redacted_and_no_send(self):
        self.path.write_text(json.dumps(fixture()),encoding="utf-8")
        stream=io.StringIO()
        mockresult=BrowserSessionInventory("OBSERVED_SESSION_OBJECTS_ONLY",
                        "CLI_OBJECT_COUNT_NOT_GPT_IDENTITY_PROOF",1)
        with mock.patch("master_a_dynamic_v4.isolated_worker_session_readiness_v4.inspect_chrome_use_session_inventory",return_value=mockresult):
            with contextlib.redirect_stdout(stream):
                self.assertEqual(0,main(["--state-file",str(self.path),"--include-live-cli-sessions",
                                    "--chrome-use-exe",str(self.path)]))
        value=json.loads(stream.getvalue())
        self.assertEqual(1,value["live_cli_sessions"]["observed_session_objects"])
        self.assertFalse(value["live_cli_sessions"]["distinct_gpt_workers_host_authenticated"])
        self.assertFalse(value["browser_send_authorized"])
        self.assertNotIn(SECRET_URL,stream.getvalue())

    def test_oversize_state_rejected_before_json_load(self):
        self.path.write_bytes(b"x"*(2*1024*1024+1))
        self.assertEqual("STATE_FILE_OVERSIZE",inspect_legacy_worker_readiness_file(self.path).reason)


if __name__=="__main__":
    unittest.main()
