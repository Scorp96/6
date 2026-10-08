from __future__ import annotations

import asyncio
import unittest

from master_a_dynamic_v4.physical_progress_probe import observe_bound_progress

URL="https://chatgpt.com/c/physical-worker"
class Fake:
    def __init__(self,snapshot=None,physical_url=URL,driver_url=URL,session="chrome-session-1",exc=None):
        self.calls=[]
        self.snapshot=snapshot if snapshot is not None else "Focused Window: Chrome\n"+URL+"\n- textbox Message ChatGPT"
        self.driver_url=driver_url
        self.physical_url=physical_url
        self.session=session
        self.exc=exc
    async def observe_current_binding(self,channel):
        self.calls.append(("observe_current_binding",channel))
        if self.exc:raise self.exc
        return {"driver_url":self.driver_url,"physical_url":self.physical_url,
                "session":self.session,"snapshot":self.snapshot}
    async def submit_prompt(self,**kw):
        raise AssertionError("browser send forbidden")
    async def snapshot_conversation(self,*args):
        raise AssertionError("URL-targeted observation would navigate")

def probe(driver,**changes):
    options={"channel":"worker-1","expected_conversation_url":URL}
    options.update(changes)
    return asyncio.run(observe_bound_progress(driver,**options))

class PhysicalProgressProbeTests(unittest.TestCase):
    def test_stable_idle_looking_screen_is_unknown_not_done(self):
        d=Fake()
        v=probe(d)
        self.assertEqual("UNKNOWN",v.status)
        self.assertEqual("NO_AFFIRMATIVE_TERMINAL_PROOF",v.reason)
        self.assertFalse(v.browser_send_authorized)
        self.assertEqual([("observe_current_binding","worker-1")],d.calls)

    def test_visible_stop_control_means_generating(self):
        d=Fake("Focused Window: Chrome\n"+URL+'\n- button "Stop generating"')
        v=probe(d)
        self.assertEqual("GENERATING",v.status)
        self.assertFalse(v.browser_send_authorized)

    def test_response_body_quoting_stop_generating_not_positive_ui_control(self):
        d=Fake("Focused Window: Chrome\n"+URL+'\nuser wrote: stop generating\n')
        self.assertEqual("UNKNOWN",probe(d).status)

    def test_url_conflict_never_navigates_or_sends(self):
        d=Fake(physical_url="https://chatgpt.com/c/different")
        v=probe(d)
        self.assertEqual("BLOCKED",v.status)
        self.assertEqual("PHYSICAL_BINDING_CONFLICT",v.reason)
        self.assertEqual(1,len(d.calls))

    def test_missing_driver_binding_is_blocked(self):
        self.assertEqual("PHYSICAL_SESSION_MISSING",probe(Fake(session="")).reason)

    def test_snapshot_requires_canonical_url_evidence(self):
        d=Fake(snapshot='- textbox Message ChatGPT')
        self.assertEqual("SNAPSHOT_URL_NOT_CONFIRMED",probe(d).reason)

    def test_bad_expected_url_fails_without_probing(self):
        d=Fake()
        v=probe(d,expected_conversation_url="https://evil.invalid/c/physical-worker")
        self.assertEqual("EXPECTED_CONVERSATION_URL_INVALID",v.reason)
        self.assertEqual([],d.calls)

    def test_observation_failure_is_not_a_wake_trigger(self):
        d=Fake(exc=TimeoutError("browser read failed"))
        self.assertEqual("PHYSICAL_OBSERVATION_FAILED:TimeoutError",probe(d).reason)

    def test_missing_observer_capability_blocked(self):
        self.assertEqual("READ_ONLY_DRIVER_CAPABILITY_MISSING",probe(object()).reason)

    def test_nonmapping_response_blocked(self):
        class Invalid:
            async def observe_current_binding(self,channel):
                return None
        self.assertEqual("PHYSICAL_OBSERVATION_INVALID",probe(Invalid()).reason)


if __name__=="__main__":
    unittest.main()
