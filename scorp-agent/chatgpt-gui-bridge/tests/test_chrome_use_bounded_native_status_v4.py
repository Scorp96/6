from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from chrome_use_loopback_observer_v4 import (
    _bounded_native_status_runner, _native_stream_port,
)

N="scorp-r2-isolated-native-wait-fixture"
PRIVATE="PRIVATE_SESSION_TOKEN https://chatgpt.com/c/private"


class BoundedNativeStreamStatusTests(unittest.TestCase):
    def capture(self, *, payload=b'{"success":true,"data":{"port":12345}}',
                err=b"", exit_code=0, timeout_first=False):
        state={"argv":None,"opts":None,"waits":[],"killed":False}
        class FakePopen:
            def __init__(self,argv,**options):
                state["argv"]=argv
                state["opts"]=options
                options["stdout"].write(payload)
                options["stdout"].flush()
                options["stderr"].write(err)
                options["stderr"].flush()
            def wait(self,timeout):
                state["waits"].append(timeout)
                if timeout_first and len(state["waits"])==1:
                    raise subprocess.TimeoutExpired("chrome-use",timeout)
                return exit_code
            def kill(self):
                state["killed"]=True
        with patch("chrome_use_loopback_observer_v4.subprocess.Popen",FakePopen):
            result=_bounded_native_status_runner(["chrome-use.exe","--json","stream","status"])
        return result,state

    def test_tempfile_capture_replaces_inherited_pipes(self):
        result,state=self.capture()
        self.assertEqual(0,result.returncode)
        self.assertIn('"port":12345',result.stdout)
        self.assertIsNot(state["opts"]["stdout"],subprocess.PIPE)
        self.assertIsNot(state["opts"]["stderr"],subprocess.PIPE)
        self.assertEqual(subprocess.DEVNULL,state["opts"]["stdin"])
        self.assertEqual([8],state["waits"])
        self.assertFalse(state["killed"])

    def test_timeout_kills_direct_process_and_returns_constant(self):
        result,state=self.capture(
            payload=PRIVATE.encode(),err=PRIVATE.encode(),timeout_first=True,
        )
        self.assertEqual(124,result.returncode)
        self.assertEqual("",result.stdout)
        self.assertTrue(state["killed"])
        self.assertEqual([8,2],state["waits"])
        self.assertNotIn(PRIVATE,repr(result))

    def test_nonzero_process_exit_drops_native_output(self):
        result,state=self.capture(
            exit_code=7,payload=PRIVATE.encode(),err=PRIVATE.encode(),
        )
        self.assertEqual(7,result.returncode)
        self.assertEqual("",result.stdout)
        self.assertNotIn(PRIVATE,repr(result))

    def test_oversized_success_stdout_is_refused(self):
        result,_=self.capture(payload=b"x"*16385)
        self.assertEqual(70,result.returncode)
        self.assertEqual("",result.stdout)

    def test_native_stream_port_uses_bounded_default_not_pipe_runner(self):
        payload=b'{"success":true,"data":{"port":23456}}'
        state={"called":False}
        class FakePopen:
            def __init__(self,argv,**opts):
                state["called"]=True
                opts["stdout"].write(payload)
                opts["stdout"].flush()
            def wait(self,timeout):return 0
            def kill(self):raise AssertionError("should not kill")
        with patch("chrome_use_loopback_observer_v4.subprocess.Popen",FakePopen):
            value=_native_stream_port("chrome-use.exe",N)
        self.assertTrue(state["called"])
        self.assertEqual(23456,value)

    def test_native_popen_error_fails_closed(self):
        def raises(*args,**kwargs):
            raise FileNotFoundError(PRIVATE)
        with patch("chrome_use_loopback_observer_v4.subprocess.Popen",raises):
            self.assertIsNone(_native_stream_port("chrome-use.exe",N))

    def test_early_success_does_not_wait_for_descendant_pipe_eof(self):
        # The fake child writes and leaves the file object alive. Once wait
        # says direct CLI has exited, the reader can seek the tempfile and
        # finish without reading any inherited pipe until EOF.
        result,state=self.capture(
            payload=b'{"success":true,"data":{"port":34567}}',
        )
        self.assertEqual(0,result.returncode)
        self.assertEqual([8],state["waits"])
        self.assertEqual(
            {"success":True,"data":{"port":34567}},
            json.loads(result.stdout),
        )


if __name__=="__main__":
    unittest.main()
