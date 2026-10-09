from __future__ import annotations

import asyncio
import unittest
from isolated_chrome_namespace_admission_v4 import (
    attempt_isolated_namespace_resume_without_tabs,
    inspect_fresh_namespace_without_tabs,
)

NAME = "scorp-r2-isolated-canary-fixture-20261009"


class FakeCLI:
    def __init__(self, *, entries=None, error=None, valid=True):
        self.entries = entries if entries is not None else [{"name": "prod-master"}]
        self.error = error
        self.valid = valid
        self.calls = []

    async def list_sessions_readonly(self, *, timeout_seconds=10):
        self.calls.append(("session", "list"))
        if self.error:
            raise self.error
        return {"ok": self.valid, "sessions": self.entries}

    async def run_json(self, *args, **kwargs):
        raise AssertionError("UNSAFE_SESSION_RESUME_MUST_NEVER_RUN")


def run(cli=None, *, namespace=NAME, timeout_seconds=10):
    return asyncio.run(inspect_fresh_namespace_without_tabs(
        cli or FakeCLI(), namespace=namespace,
        timeout_seconds=timeout_seconds,
    ))


class IsolatedChromeNamespaceAdmissionTests(unittest.TestCase):
    def test_new_name_only_yields_unattested_preflight(self):
        cli = FakeCLI()
        result = run(cli)
        self.assertEqual("NAMESPACE_NAME_AVAILABLE_UNATTESTED", result.status)
        self.assertEqual("NEW_NAMESPACE_NAME_AVAILABLE_NOT_PHYSICAL_OWNERSHIP_PROOF", result.reason)
        self.assertEqual(1, result.before_session_count)
        self.assertEqual([("session", "list")], cli.calls)
        self.assertFalse(result.tab_navigation_authorized)
        self.assertFalse(result.browser_send_authorized)

    def test_existing_namespace_cannot_be_adopted(self):
        cli = FakeCLI(entries=[{"name": NAME}])
        self.assertEqual("ISOLATED_NAMESPACE_ALREADY_EXISTS", run(cli).reason)
        self.assertEqual([("session", "list")], cli.calls)

    def test_legacy_resume_creation_function_is_permanently_blocked(self):
        cli = FakeCLI()
        result = asyncio.run(attempt_isolated_namespace_resume_without_tabs(
            cli, namespace=NAME,
        ))
        self.assertEqual("SESSION_RESUME_HANDOFF_ONLY_NOT_SESSION_CREATION", result.reason)
        self.assertEqual([], cli.calls)
        self.assertFalse(result.browser_send_authorized)

    def test_bad_names_block_without_calling_chrome(self):
        cli = FakeCLI()
        for name in (None, "", "master", "worker-1", "scorp-r2-isolated-x",
                     "scorp-r2-isolated-" + "a" * 70):
            with self.subTest(name=name):
                self.assertEqual(
                    "ISOLATED_NAMESPACE_OR_TIMEOUT_INVALID",
                    run(cli, namespace=name).reason,
                )
        self.assertEqual([], cli.calls)

    def test_bad_timeouts_block_without_calling_chrome(self):
        cli = FakeCLI()
        for val in (0, -1, True, 31, 0.4):
            with self.subTest(val=val):
                self.assertEqual(
                    "ISOLATED_NAMESPACE_OR_TIMEOUT_INVALID",
                    run(cli, timeout_seconds=val).reason,
                )
        self.assertEqual([], cli.calls)

    def test_duplicate_session_names_fail_closed(self):
        cli = FakeCLI(entries=[{"name": "prod"}, {"name": "prod"}])
        self.assertEqual("PRE_CREATION_INVENTORY_INVALID", run(cli).reason)

    def test_invalid_session_type_fails_closed(self):
        cli = FakeCLI(entries=["prod"])
        self.assertEqual("PRE_CREATION_INVENTORY_INVALID", run(cli).reason)

    def test_inventory_error_is_redacted(self):
        secret = "token-super-secret https://chatgpt.com/c/private"
        cli = FakeCLI(error=RuntimeError(secret))
        result = run(cli)
        self.assertEqual("PRE_CREATION_INVENTORY_UNAVAILABLE", result.reason)
        self.assertNotIn(secret, str(result))

    def test_invalid_inventory_state_fails_closed(self):
        cli = FakeCLI(valid=False)
        self.assertEqual("PRE_CREATION_INVENTORY_INVALID", run(cli).reason)

    def test_no_cli_capability_is_blocked(self):
        self.assertEqual("READONLY_SESSION_CAPABILITY_UNAVAILABLE", run(object()).reason)

    def test_empty_existing_inventory_still_does_not_authorize_tabs(self):
        result = run(FakeCLI(entries=[]))
        self.assertEqual(0, result.before_session_count)
        self.assertFalse(result.tab_navigation_authorized)
        self.assertFalse(result.physical_isolation_attested)
        self.assertEqual(0, result.model_calls)


if __name__ == "__main__":
    unittest.main()
