"""Fail-closed browser root ownership review: never authorize process kill."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
import unittest

from master_a_dynamic_v4.isolated_browser_process_ownership_v4 import (
    ChromeResourceSnapshot, ChromeProcessObservation, ProcessOwnershipWitness,
    assess_residual_chrome_resources, review_host_process_witness,
)

KEY=b"host-side-test-only-key-of-32-bytes-not-a-production-secret"
NONCE="a"*32
NAMESPACE="scorp-r2-worker2-test-owned-browser-20261009"
PROFILE=hashlib.sha256(b"fake-local-profile-test").hexdigest()


def sign(w):
    fields=dict(vars(w))
    fields.pop("signature")
    canonical=json.dumps(fields,sort_keys=True,separators=(",",":")).encode()
    sig=hmac.new(KEY,canonical,hashlib.sha256).hexdigest()
    return replace(w,signature=sig)


def pair():
    w=ProcessOwnershipWitness(
        protocol="scorp.r2.host-owned-chrome-process/1",
        attempt_id=NONCE,namespace=NAMESPACE,process_id=12345,
        process_created_epoch_ms=1781000000000,
        user_data_dir_sha256=PROFILE,windows_session_id=1,
        witness_issued_epoch_ms=1780999995000,
        signature="",
    )
    return sign(w),ChromeProcessObservation(
        process_id=12345,process_created_epoch_ms=1781000000000,
        user_data_dir_sha256=PROFILE,windows_session_id=1,
    )


def check(w,o,*,key=KEY,nonce=NONCE,namespace=NAMESPACE):
    return review_host_process_witness(
        w,o,host_attestation_key=key,expected_attempt_id=nonce,
        expected_namespace=namespace,
    )


class ProcessOwnershipNoKillTests(unittest.TestCase):
    def test_actual_residual_21_roots_missing_parents_blocks_cleanup(self):
        snap=ChromeResourceSnapshot(206,22,21,21,20,0,8265,7521)
        got=assess_residual_chrome_resources(snap)
        self.assertEqual("HUMAN_ATTRIBUTION_REQUIRED",got.status)
        self.assertFalse(got.cleanup_authorized)
        self.assertFalse(got.browser_launch_authorized)
        self.assertFalse(got.browser_send_authorized)
        self.assertEqual(0,got.browser_processes_terminated)

    def test_old_processes_only_still_dont_grant_new_browser_launch(self):
        got=assess_residual_chrome_resources(
            ChromeResourceSnapshot(17,1,0,0,0,0,10000,800))
        self.assertEqual("NO_RESIDUAL_ROOTS_OBSERVED",got.status)
        self.assertFalse(got.browser_launch_authorized)

    def test_low_available_ram_blocks_even_without_new_roots(self):
        got=assess_residual_chrome_resources(
            ChromeResourceSnapshot(7,1,0,0,0,0,1024,700))
        self.assertEqual("FREE_MEMORY_BELOW_ISOLATED_BROWSER_THRESHOLD",got.reason)

    def test_negative_inconsistent_and_boolean_counters_fail_closed(self):
        for snap in (
            ChromeResourceSnapshot(-1,22,21,21,20,0,8265,7521),
            ChromeResourceSnapshot(10,22,21,21,20,0,8265,7521),
            ChromeResourceSnapshot(100,22,23,21,20,0,8265,7521),
            ChromeResourceSnapshot(100,22,21,22,20,0,8265,7521),
            ChromeResourceSnapshot(100,22,21,21,22,0,8265,7521),
            ChromeResourceSnapshot(100,22,21,21,20,22,8265,7521),
            ChromeResourceSnapshot(True,22,21,21,20,0,8265,7521),
        ):
            with self.subTest(snap=snap):
                got=assess_residual_chrome_resources(snap)
                self.assertEqual("BLOCKED",got.status)
                self.assertFalse(got.cleanup_authorized)

    def test_trusted_future_signed_witness_still_not_kill_authorization(self):
        w,o=pair()
        result=check(w,o)
        self.assertEqual("OWNERSHIP_EVIDENCE_FOR_HUMAN_REVIEW",result.status)
        self.assertTrue(result.host_process_identity_attested)
        self.assertFalse(result.cleanup_authorized)
        self.assertFalse(result.browser_launch_authorized)
        self.assertFalse(result.browser_send_authorized)

    def test_absent_host_signature_key_blocks_adoption(self):
        w,o=pair()
        for key in (None,b"",b"too-short"):
            self.assertEqual("TRUSTED_HOST_ATTESTATION_KEY_MISSING",check(w,o,key=key).reason)

    def test_invalid_hmac_fails_even_when_pid_and_profile_match(self):
        w,o=pair()
        self.assertEqual("HOST_WITNESS_SIGNATURE_INVALID",check(replace(w,signature="f"*64),o).reason)

    def test_exact_nonce_and_namespace_are_required(self):
        w,o=pair()
        for nonce,namespace in ((NONCE,"scorp-r2-worker2-wrong-namespace-20261009"),
                                ("b"*32,NAMESPACE)):
            self.assertEqual("WITNESS_ATTEMPT_SCOPE_MISMATCH",check(w,o,nonce=nonce,namespace=namespace).reason)

    def test_changed_pid_with_same_profile_rejected(self):
        w,o=pair()
        self.assertEqual("PROCESS_IDENTITY_OR_PROFILE_CHANGED",
                         check(w,replace(o,process_id=12346)).reason)

    def test_pid_reuse_with_new_creation_time_rejected(self):
        w,o=pair()
        self.assertEqual("PROCESS_IDENTITY_OR_PROFILE_CHANGED",
                         check(w,replace(o,process_created_epoch_ms=o.process_created_epoch_ms+10)).reason)

    def test_profile_substitution_rejected(self):
        w,o=pair()
        self.assertEqual("PROCESS_IDENTITY_OR_PROFILE_CHANGED",
                         check(w,replace(o,user_data_dir_sha256="b"*64)).reason)

    def test_other_windows_session_rejected(self):
        w,o=pair()
        self.assertEqual("PROCESS_IDENTITY_OR_PROFILE_CHANGED",
                         check(w,replace(o,windows_session_id=0)).reason)

    def test_witness_created_after_process_start_rejected(self):
        w,o=pair()
        w=sign(replace(w,witness_issued_epoch_ms=w.process_created_epoch_ms+1))
        self.assertEqual("WITNESS_NOT_ISSUED_AT_OR_BEFORE_LAUNCH",check(w,o).reason)

    def test_backdated_witness_too_far_before_launch_rejected(self):
        w,o=pair()
        w=sign(replace(w,witness_issued_epoch_ms=w.process_created_epoch_ms-180001))
        self.assertEqual("WITNESS_NOT_ISSUED_AT_OR_BEFORE_LAUNCH",check(w,o).reason)

    def test_untyped_boolean_pid_and_malformed_digest_rejected(self):
        w,o=pair()
        self.assertEqual("PROCESS_IDENTITY_FIELD_INVALID",check(w,replace(o,process_id=True)).reason)
        self.assertEqual("OBSERVED_PROFILE_DIGEST_INVALID",
                         check(w,replace(o,user_data_dir_sha256="NOTHEX")).reason)

    def test_malicious_or_missing_witness_does_not_claim_provenance(self):
        _,o=pair()
        for value in (None,{},[],False,123):
            got=check(value,o)
            self.assertEqual("WITNESS_SCHEMA_INVALID",got.reason)
            self.assertFalse(got.host_process_identity_attested)

    def test_host_nonce_or_namespace_malformed_fail_closed(self):
        w,o=pair()
        self.assertEqual("EXPECTED_LAUNCH_BINDING_INVALID",check(w,o,nonce="BAD").reason)
        self.assertEqual("EXPECTED_LAUNCH_BINDING_INVALID",check(w,o,namespace="master").reason)

    def test_results_do_not_contain_pid_or_profile_or_key(self):
        w,o=pair()
        for result in (check(w,o),assess_residual_chrome_resources(
                ChromeResourceSnapshot(206,22,21,21,20,0,8265,7521))):
            raw=repr(result)
            self.assertNotIn("12345",raw)
            self.assertNotIn(PROFILE,raw)
            self.assertNotIn(NAMESPACE,raw)
            self.assertNotIn(KEY.decode(),raw)

    def test_no_process_or_browser_action_entrypoints(self):
        import master_a_dynamic_v4.isolated_browser_process_ownership_v4 as obj
        for name in ("kill","stop_process","terminate","launch_browser",
                     "open_browser","submit_prompt","wake_master","authorize_cleanup"):
            self.assertFalse(hasattr(obj,name))


if __name__=="__main__":
    unittest.main()
