from __future__ import annotations

import asyncio
import unittest

from isolated_chrome_namespace_admission_v4 import (
    attempt_isolated_namespace_resume_without_tabs,
)

NAME = "scorp-r2-isolated-canary-fixture-20261009"


class FakeCLI:
    def __init__(self, *, initial=(), create=False, accepted=True,
                 failure=None, post_invalid=False, concurrent=False):
        self.sessions = set(initial)
        self.create = create
        self.accepted = accepted
        self.failure = failure
        self.post_invalid = post_invalid
        self.concurrent = concurrent
        self.calls = []
        self.list_count = 0

    async def list_sessions_readonly(self, *, timeout_seconds=10):
        self.calls.append(("session", "list"))
        self.list_count += 1
        if self.post_invalid and self.list_count > 1:
            return {"ok": False, "sessions": []}
        return {
            "ok": True,
            "sessions": [{"name": name, "owner": "no-attestation"} for name in sorted(self.sessions)],
        }

    async def run_json(self, namespace, *args, timeout_seconds=10):
        self.calls.append((namespace, *args))
        if self.failure:
            raise RuntimeError("SECRET https://chatgpt.com/c/private")
        if self.create:
            self.sessions.add(namespace)
        if self.concurrent:
            self.sessions.add("unrelated-live-session")
        return {"success": self.accepted}


def run(cli=None, *, namespace=NAME, timeout_seconds=10):
    return asyncio.run(attempt_isolated_namespace_resume_without_tabs(
        cli if cli is not None else FakeCLI(),
        namespace=namespace, timeout_seconds=timeout_seconds,
    ))


class IsolatedChromeNamespaceAdmissionTests(unittest.TestCase):
    def test_windows_observed_resume_success_but_no_new_session_blocks(self):
        cli = FakeCLI(create=False, accepted=True)
        result = run(cli)
        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("RESUME_DID_NOT_CREATE_NAMESPACE", result.reason)
        self.assertTrue(result.resume_command_accepted)
        self.assertEqual(0, result.before_session_count)
        self.assertEqual(0, result.after_session_count)
        self.assertFalse(result.newly_observed_namespace)
        self.assertFalse(result.tab_navigation_authorized)
        self.assertFalse(result.browser_send_authorized)
        self.assertEqual([
            ("session", "list"), (NAME, "session", "resume"), ("session", "list")
        ], cli.calls)

    def test_newly_observed_namespace_still_unattested_and_no_send(self):
        cli = FakeCLI(initial=("existing-prod",), create=True)
        result = run(cli)
        self.assertEqual("ISOLATED_NAMESPACE_CANDIDATE_UNATTESTED", result.status)
        self.assertTrue(result.newly_observed_namespace)
        self.assertEqual(1, result.before_session_count)
        self.assertEqual(2, result.after_session_count)
        self.assertFalse(result.physical_isolation_attested)
        self.assertFalse(result.tab_navigation_authorized)
        self.assertFalse(result.browser_send_authorized)
        self.assertEqual(0, result.model_calls)

    def test_existing_namespace_is_never_resumed_or_adopted(self):
        cli = FakeCLI(initial=(NAME,))
        result = run(cli)
        self.assertEqual("ISOLATED_NAMESPACE_ALREADY_EXISTS", result.reason)
        self.assertEqual([("session", "list")], cli.calls)

    def test_missing_namespace_or_improper_name_rejected_before_browser(self):
        cli = FakeCLI()
        for item in ("", "master", "worker-1", "scorp-r2-isolated-abcd", None):
            with self.subTest(item=item):
                self.assertEqual(
                    "ISOLATED_NAMESPACE_OR_TIMEOUT_INVALID",
                    run(cli, namespace=item).reason,
                )
        self.assertEqual([], cli.calls)

    def test_invalid_timeout_rejected_before_browser(self):
        cli = FakeCLI()
        for timeout in (0, -1, 31, True, 1.5):
            with self.subTest(timeout=timeout):
                self.assertEqual(
                    "ISOLATED_NAMESPACE_OR_TIMEOUT_INVALID",
                    run(cli, timeout_seconds=timeout).reason,
                )
        self.assertEqual([], cli.calls)

    def test_resume_exception_is_ambiguous_and_never_retried(self):
        cli = FakeCLI(failure=True)
        result = run(cli)
        self.assertEqual("RESUME_EFFECT_UNKNOWN", result.reason)
        self.assertEqual(2, len(cli.calls))
        self.assertNotIn("SECRET", str(result))
        self.assertFalse(result.browser_send_authorized)

    def test_post_resume_inventory_failure_is_unknown_not_created(self):
        cli = FakeCLI(create=True, post_invalid=True)
        result = run(cli)
        self.assertEqual("POST_RESUME_INVENTORY_INVALID", result.reason)
        self.assertFalse(result.newly_observed_namespace)
        self.assertFalse(result.tab_navigation_authorized)

    def test_post_resume_new_name_with_failed_return_stays_unverified(self):
        cli = FakeCLI(create=True, accepted=False)
        result = run(cli)
        self.assertEqual("RESUME_RETURN_INDETERMINATE", result.reason)
        self.assertTrue(result.newly_observed_namespace)
        self.assertFalse(result.tab_navigation_authorized)

    def test_unrelated_concurrent_namespace_addition_blocks(self):
        cli = FakeCLI(create=True, concurrent=True)
        result = run(cli)
        self.assertEqual("UNEXPECTED_CONCURRENT_SESSION_MUTATION", result.reason)
        self.assertFalse(result.browser_send_authorized)

    def test_duplicate_session_rows_are_invalid(self):
        class Duplicate(FakeCLI):
            async def list_sessions_readonly(self, *, timeout_seconds=10):
                self.calls.append(("session", "list"))
                return {"ok": True, "sessions": [
                    {"name": "old"}, {"name": "old"},
                ]}
        result = run(Duplicate())
        self.assertEqual("PRE_RESUME_INVENTORY_INVALID", result.reason)

    def test_no_execution_capability_implied_by_valid_namespace(self):
        result = run(FakeCLI(create=True))
        for flag in (
            result.browser_send_authorized,
            result.tab_navigation_authorized,
            result.physical_isolation_attested,
        ):
            self.assertFalse(flag)


if __name__ == "__main__":
    unittest.main()
