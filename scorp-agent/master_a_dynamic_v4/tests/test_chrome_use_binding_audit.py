from __future__ import annotations

import json
import subprocess
import unittest

from master_a_dynamic_v4.chrome_use_binding_audit import inspect_existing_chrome_use_session

MASTER = "https://chatgpt.com/c/master-existing"
OTHER = "https://chatgpt.com/c/other"
EXECUTABLE = r"C:\Tools\chrome-use.exe"


class FakeChromeCli:
    def __init__(self, sessions=("scorp-a",), focused=MASTER, tabs=None):
        self.sessions = list(sessions)
        self.focused = focused
        self.tabs = [MASTER] if tabs is None else list(tabs)
        self.calls = []
        self.fail_on = None
        self.timeout_on = None

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.check_command(argv)
        self._check_options(kw)
        suffix = argv[1:]
        if suffix == ["--json", "session", "list"]:
            body = {"sessions": list(self.sessions)}
        elif suffix == ["--session", "scorp-a", "--json", "get", "url"]:
            body = {"data": {"url": self.focused}}
        elif suffix == ["--session", "scorp-a", "--json", "tab", "list"]:
            body = {"tabs": [{"url": url} for url in self.tabs]}
        else:
            raise AssertionError("UNEXPECTED_BROWSER_OPERATION")
        if suffix == self.timeout_on:
            raise subprocess.TimeoutExpired(argv, 10)
        if suffix == self.fail_on:
            return subprocess.CompletedProcess(argv, 2, "", "private error")
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    @staticmethod
    def _check_options(options):
        assert options["capture_output"] is True
        assert options["text"] is True
        assert options["timeout"] <= 30

    @staticmethod
    def check_command(argv):
        assert argv[0] == EXECUTABLE
        assert argv[1:] in [
            ["--json", "session", "list"],
            ["--session", "scorp-a", "--json", "get", "url"],
            ["--session", "scorp-a", "--json", "tab", "list"],
        ]


def inspect(cli, **kw):
    settings={"unresolved_master_intents":0,"rotation_conflict":False}
    settings.update(kw)
    return inspect_existing_chrome_use_session(EXECUTABLE, MASTER, run=cli, **settings)


class ChromeUseBindingAuditTests(unittest.TestCase):
    def test_unique_exact_selected_master_only_allows_readonly_review(self):
        cli = FakeChromeCli()
        result = inspect(cli)
        self.assertEqual("READONLY_MATCH_REVIEW_REQUIRED", result.status)
        self.assertEqual(1, result.matching_master_tabs)
        self.assertTrue(result.selected_url_matches_master)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.browser_adoption_authorized)
        self.assertEqual(3, len(cli.calls))

    def test_zero_driver_sessions_with_one_chrome_session_cannot_auto_bind_master(self):
        cli = FakeChromeCli(focused="https://example.org", tabs=[OTHER])
        result = inspect(cli)
        self.assertEqual("BLOCKED", result.status)
        self.assertEqual("CANONICAL_MASTER_TAB_NOT_FOUND", result.reason)

    def test_live_chatgpt_tab_not_equal_to_original_master_blocks(self):
        cli = FakeChromeCli(focused="https://example.org", tabs=[OTHER])
        result = inspect(cli)
        self.assertEqual("CANONICAL_MASTER_TAB_NOT_FOUND", result.reason)
        self.assertEqual(1, result.chatgpt_conversations)

    def test_original_master_in_background_does_not_allow_implicit_tab_selection(self):
        cli = FakeChromeCli(focused="https://example.org", tabs=[MASTER])
        result = inspect(cli)
        self.assertEqual("MASTER_TAB_NOT_CURRENTLY_BOUND", result.reason)
        self.assertFalse(result.browser_adoption_authorized)

    def test_legacy_ambiguous_master_blocks_even_when_url_matches(self):
        cli = FakeChromeCli()
        result = inspect(cli, unresolved_master_intents=1)
        self.assertEqual("LEGACY_MASTER_INTENT_UNRESOLVED", result.reason)
        self.assertFalse(result.browser_send_authorized)

    def test_known_rotation_conflict_blocks_even_if_current_url_matches(self):
        cli=FakeChromeCli()
        result=inspect(cli,rotation_conflict=True)
        self.assertEqual("MASTER_ROTATION_CONFLICT_UNRESOLVED",result.reason)
        self.assertFalse(result.browser_send_authorized)

    def test_missing_rotation_status_refused_before_cli(self):
        cli=FakeChromeCli()
        result=inspect_existing_chrome_use_session(
            EXECUTABLE,MASTER,run=cli,unresolved_master_intents=0,
        )
        self.assertEqual("ROTATION_STATUS_UNVERIFIED",result.reason)
        self.assertEqual([],cli.calls)

    def test_duplicate_master_tabs_not_safe_to_choose(self):
        cli = FakeChromeCli(tabs=[MASTER, MASTER])
        result = inspect(cli)
        self.assertEqual("MASTER_TAB_DUPLICATED", result.reason)

    def test_multiple_live_sessions_cannot_be_guessed(self):
        cli = FakeChromeCli(sessions=["scorp-a", "scorp-b"])
        result = inspect(cli)
        self.assertEqual("UNIQUE_SESSION_REQUIRED", result.reason)
        self.assertEqual(1, len(cli.calls))

    def test_missing_live_session_does_not_create_one(self):
        cli = FakeChromeCli(sessions=[])
        result = inspect(cli)
        self.assertEqual("UNIQUE_SESSION_REQUIRED", result.reason)
        self.assertEqual(0, result.discovered_sessions)
        self.assertEqual(1, len(cli.calls))

    def test_different_host_cannot_pose_as_master(self):
        cli = FakeChromeCli(focused="https://chatgpt.com.attacker.invalid/c/master-existing",
                            tabs=["https://chatgpt.com.attacker.invalid/c/master-existing"])
        result = inspect(cli)
        self.assertEqual("CANONICAL_MASTER_TAB_NOT_FOUND", result.reason)

    def test_session_cli_failure_does_not_leak_raw_stdout(self):
        cli = FakeChromeCli()
        cli.fail_on=["--json", "session", "list"]
        result = inspect(cli)
        self.assertEqual("SESSION_INVENTORY_UNAVAILABLE", result.reason)
        self.assertNotIn("private",repr(result))

    def test_non_successful_url_probe_blocks(self):
        cli = FakeChromeCli()
        cli.fail_on=["--session", "scorp-a", "--json", "get", "url"]
        self.assertEqual("READ_ONLY_SESSION_OBSERVATION_FAILED", inspect(cli).reason)

    def test_timeout_does_not_trigger_browser_recovery(self):
        cli = FakeChromeCli()
        cli.timeout_on=["--session", "scorp-a", "--json", "tab", "list"]
        result = inspect(cli)
        self.assertEqual("READ_ONLY_SESSION_OBSERVATION_FAILED", result.reason)
        self.assertEqual(3, len(cli.calls))

    def test_unknown_identifier_must_not_be_executed(self):
        cli = FakeChromeCli(sessions=[{"name": "bad session; open URL"}])
        result = inspect(cli)
        self.assertEqual("SESSION_IDENTIFIER_UNVERIFIED", result.reason)
        self.assertEqual(1, len(cli.calls))

    def test_missing_authoritative_intent_count_is_refused(self):
        cli=FakeChromeCli()
        result=inspect_existing_chrome_use_session(EXECUTABLE,MASTER,run=cli)
        self.assertEqual("INTENT_COUNT_UNVERIFIED",result.reason)
        self.assertEqual([],cli.calls)

    def test_invalid_master_url_refused_before_cli(self):
        cli = FakeChromeCli()
        result = inspect_existing_chrome_use_session(
            EXECUTABLE, "https://evil.invalid/c/master-existing", run=cli,
        )
        self.assertEqual("MASTER_CANONICAL_URL_UNVERIFIED", result.reason)
        self.assertEqual([], cli.calls)

    def test_invalid_legacy_ambiguity_count_refused(self):
        cli = FakeChromeCli()
        self.assertEqual("INTENT_COUNT_UNVERIFIED",
                         inspect(cli, unresolved_master_intents=True).reason)
        self.assertEqual([], cli.calls)


if __name__ == "__main__":
    unittest.main()
