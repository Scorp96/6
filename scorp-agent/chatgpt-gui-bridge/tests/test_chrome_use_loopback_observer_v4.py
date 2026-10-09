from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from chrome_use_loopback_observer_v4 import (
    _NoRedirect, _native_stream_port, _read_local_json,
    observe_local_chrome_status_no_send,
)

NAME = "scorp-r2-isolated-loopback-test-session"


def runner_for_port(port=12345, *, success=True, rc=0, malformed=False):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        content = "non-json" if malformed else json.dumps({
            "success": success, "data": {"port": port},
        })
        return SimpleNamespace(returncode=rc, stdout=content)
    run.calls = calls
    return run


def fake_reader(port, resource):
    assert port == 12345
    return {
        "/api/v1/status": {"engine": "READY", "private_url": "SECRET"},
        "/api/v1/tabs": [{"tabId": "PRIVATE-ID", "url": "SECRET"}],
        "/api/v1/sessions": [{"name": "PRIVATE-SESSION"}, {"name": NAME}],
    }[resource]


class ChromeUseLoopbackObserverTests(unittest.TestCase):
    def observe(self, *, runner=None, reader=fake_reader, namespace=NAME):
        return observe_local_chrome_status_no_send(
            executable="chrome-use.exe",
            pinned_isolated_namespace=namespace,
            runner=runner or runner_for_port(),
            reader=reader,
        )

    def check_safe(self, status):
        self.assertFalse(status.native_final_event_verified)
        self.assertFalse(status.authentication_verified)
        self.assertFalse(status.browser_send_authorized)
        self.assertFalse(status.local_execution_authorized)
        self.assertEqual(0, status.local_model_calls)
        self.assertNotIn("SECRET",repr(status))
        self.assertNotIn("PRIVATE",repr(status))

    def test_real_windows_shape_status_sessions_and_tabs(self):
        result = self.observe()
        self.assertEqual("LOCAL_STATE_OBSERVED_UNATTESTED",result.status)
        self.assertEqual(2,result.session_count)
        self.assertEqual(1,result.tab_count)
        self.assertTrue(result.status_endpoint_readable)
        self.assertTrue(result.all_three_endpoints_readable)
        self.check_safe(result)

    def test_exact_readonly_cli_argv_and_bounded_timeout(self):
        runner=runner_for_port()
        self.assertEqual("LOCAL_STATE_OBSERVED_UNATTESTED",
                         self.observe(runner=runner).status)
        self.assertEqual(1,len(runner.calls))
        argv,kw=runner.calls[0]
        self.assertEqual([
            "chrome-use.exe","--session",NAME,"--json","stream","status",
        ],argv)
        self.assertEqual(8,kw["timeout"])
        self.assertTrue(kw["capture_output"])

    def test_unknown_master_namespace_never_issues_command(self):
        runner=runner_for_port()
        for name in ("master", "", None, "scorp-r1", "worker-1"):
            with self.subTest(name=name):
                result=self.observe(runner=runner,namespace=name)
                self.assertEqual("PINNED_ISOLATED_SESSION_REQUIRED",result.reason)
                self.check_safe(result)
        self.assertEqual([],runner.calls)

    def test_false_and_string_ports_are_not_coerced_to_int(self):
        for value in (True,False,"12345","http://evil:12345",None):
            with self.subTest(value=value):
                self.assertEqual(None,_native_stream_port(
                    "chrome-use.exe",NAME,runner=runner_for_port(value),
                ))

    def test_out_of_range_ports_are_rejected(self):
        for port in (0,-1,80,1023,65536,100000):
            with self.subTest(port=port):
                result=self.observe(runner=runner_for_port(port))
                self.assertEqual("VALID_NATIVE_STREAM_PORT_NOT_VERIFIED",
                                 result.reason)
                self.check_safe(result)

    def test_failed_or_malformed_native_command_has_no_port(self):
        for runner in (
            runner_for_port(rc=1),
            runner_for_port(success=False),
            runner_for_port(malformed=True),
        ):
            with self.subTest(runner=runner):
                result=self.observe(runner=runner)
                self.assertEqual("VALID_NATIVE_STREAM_PORT_NOT_VERIFIED",
                                 result.reason)
                self.check_safe(result)

    def test_native_exception_is_not_leaked_to_host_logs(self):
        def raises(*a, **kw):
            raise OSError("SECRET PRIVATE session url token")
        result=self.observe(runner=raises)
        self.assertEqual("VALID_NATIVE_STREAM_PORT_NOT_VERIFIED",result.reason)
        self.check_safe(result)

    def test_three_fixed_get_endpoints_only(self):
        paths=[]
        def read(port,path):
            paths.append(path)
            return fake_reader(port,path)
        self.observe(reader=read)
        self.assertEqual([
            "/api/v1/status","/api/v1/tabs","/api/v1/sessions",
        ],paths)
        self.assertEqual(3,len(paths))

    def test_missing_status_or_tabs_or_sessions_fails_closed(self):
        for missing in (
            "/api/v1/status","/api/v1/tabs","/api/v1/sessions",
        ):
            def read(port,path):
                return None if path==missing else fake_reader(port,path)
            result=self.observe(reader=read)
            self.assertEqual("LOOPBACK_STATUS_OR_INVENTORY_UNVERIFIED",
                             result.reason)
            self.check_safe(result)

    def test_read_exception_redacts_private_data(self):
        def raises(port,path):
            raise OSError("SECRET PRIVATE login cookie")
        result=self.observe(reader=raises)
        self.assertEqual("LOOPBACK_READ_FAILED",result.reason)
        self.check_safe(result)

    def test_nested_api_envelopes_supported_without_values_exposed(self):
        def read(port,path):
            if path.endswith("/status"):return {"data":{"status":"READY"}}
            if path.endswith("/tabs"):return {"data":{"tabs":[{"url":"SECRET"}]}}
            return {"sessions":[{"name":"SECRET"}]}
        result=self.observe(reader=read)
        self.assertEqual("LOCAL_STATE_OBSERVED_UNATTESTED",result.status)
        self.assertEqual(1,result.session_count)
        self.assertEqual(1,result.tab_count)
        self.check_safe(result)

    def test_bounded_counts_reject_large_response_arrays(self):
        def read(port,path):
            if path.endswith("/status"):return {}
            return [{}]*257
        result=self.observe(reader=read)
        self.assertEqual("LOOPBACK_STATUS_OR_INVENTORY_UNVERIFIED",result.reason)

    def test_no_redirect_is_allowed(self):
        obj=_NoRedirect()
        self.assertIsNone(obj.redirect_request(None,None,302,"",{},"https://evil.test"))

    def test_reader_never_accepts_arbitrary_path_or_port(self):
        for args in ((12345,"/secret"),(12345,"http://evil"),
                     (None,"/api/v1/status"),("12345","/api/v1/status"),
                     (80,"/api/v1/status")):
            with self.subTest(args=args):
                self.assertIsNone(_read_local_json(*args))

    def test_real_http_reader_is_loopback_only_get_no_credentials(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,size):
                self.max=size
                return b'{"ok":true}'
        class Opener:
            def __init__(self):self.req=None
            def open(self,req,timeout):
                self.req=req
                self.timeout=timeout
                return Response()
        obj=Opener()
        with patch("chrome_use_loopback_observer_v4.urllib.request.build_opener",
                   return_value=obj):
            result=_read_local_json(12345,"/api/v1/status")
        self.assertEqual({"ok":True},result)
        self.assertEqual("http://127.0.0.1:12345/api/v1/status",obj.req.full_url)
        self.assertEqual("GET",obj.req.get_method())
        self.assertEqual(3,obj.timeout)
        self.assertNotIn("Authorization",obj.req.headers)

    def test_oversized_http_payload_is_rejected(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b"x"*n
        class Opener:
            def open(self,req,timeout):return Response()
        with patch("chrome_use_loopback_observer_v4.urllib.request.build_opener",
                   return_value=Opener()):
            self.assertIsNone(_read_local_json(12345,"/api/v1/tabs"))


if __name__ == "__main__":
    unittest.main()
