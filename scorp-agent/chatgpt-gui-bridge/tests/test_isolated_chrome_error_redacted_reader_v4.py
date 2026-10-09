from __future__ import annotations

import asyncio
import json
import unittest

from isolated_chrome_error_redacted_reader_v4 import (
    SafeReadOnlyChromeUseV4,
)

N = "scorp-r2-isolated-error-redaction-fixture"
PRIVATE = "PRIVATE_SECRET https://chatgpt.com/c/account-sensitive"


def payload_sessions(names=None):
    return {"ok": True, "sessions": [
        {"name": value} for value in (names if names is not None else ["prod", N])
    ]}


def one_tab(*, owner="created", url="https://chatgpt.com/",
            tab_id="tab-1", target="target-1", attached=True):
    return {
        "tabId": tab_id, "targetId": target,
        "url": url, "ownership": owner,
        "type": "page", "relayAttached": attached,
    }


def payload_tabs(tabs=None):
    return {"success": True, "data": {
        "full": True,
        "tabs": tabs if tabs is not None else [one_tab()],
    }}


def transport(*, sessions=None, tabs=None, rc=0, stderr="", malformed=False):
    calls=[]
    async def run(argv, timeout):
        calls.append(tuple(argv))
        if rc:
            return (rc, PRIVATE, stderr or PRIVATE)
        if malformed:
            return (0, PRIVATE + "{invalid-json}", PRIVATE)
        if "session" in argv and "list" in argv:
            result=sessions if sessions is not None else payload_sessions()
        else:
            result=tabs if tabs is not None else payload_tabs()
        return (0,json.dumps(result),stderr)
    run.calls=calls
    return run


def reader(*args,runner=None,**kwargs):
    return SafeReadOnlyChromeUseV4(
        executable="chrome-use.exe",
        transport_runner=runner if runner is not None else transport(),
    )


class IsolatedChromeErrorRedactedReaderTests(unittest.TestCase):
    def test_session_count_without_session_identity_leak(self):
        c=reader(runner=transport(sessions=payload_sessions(["prod-"+PRIVATE,N])))
        result=asyncio.run(c.inspect_session_counts())
        self.assertEqual("READONLY_COUNTS_UNATTESTED",result.status)
        self.assertEqual(2,result.active_sessions)
        self.assertFalse(result.terminal_event_verified)
        self.assertFalse(result.browser_send_authorized)
        self.assertFalse(result.source_error_exposed)
        self.assertNotIn(PRIVATE,repr(result))

    def test_owned_home_category_without_raw_url_or_ids(self):
        c=reader(runner=transport(tabs=payload_tabs([
            one_tab(),
            one_tab(tab_id="tab-2",target="target-2",url="about:blank"),
        ])))
        result=asyncio.run(c.inspect_owned_tabs(isolated_namespace=N))
        self.assertEqual("READONLY_COUNTS_UNATTESTED",result.status)
        self.assertEqual(2,result.owned_tabs)
        self.assertEqual(1,result.blank_tabs)
        self.assertEqual(1,result.chatgpt_home_tabs)
        self.assertFalse(result.browser_send_authorized)
        for value in ("tab-1","target-1","https://chatgpt.com/"):
            self.assertNotIn(value,repr(result))

    def test_nonzero_returncode_discards_stdout_and_stderr(self):
        fake=transport(rc=7,stderr=PRIVATE)
        result=asyncio.run(reader(runner=fake).inspect_session_counts())
        self.assertEqual("SESSION_LIST_UNAVAILABLE",result.reason)
        self.assertNotIn(PRIVATE,repr(result))
        self.assertEqual(1,len(fake.calls))

    def test_invalid_json_input_content_not_reported(self):
        result=asyncio.run(reader(runner=transport(malformed=True)).inspect_session_counts())
        self.assertEqual("SESSION_LIST_UNAVAILABLE",result.reason)
        self.assertNotIn(PRIVATE,repr(result))

    def test_exception_in_subprocess_is_sanitized(self):
        async def raises(argv,timeout):
            raise OSError(PRIVATE)
        result=asyncio.run(reader(runner=raises).inspect_session_counts())
        self.assertEqual("SESSION_LIST_UNAVAILABLE",result.reason)
        self.assertNotIn(PRIVATE,repr(result))

    def test_large_stdout_blocks_without_copying(self):
        async def giant(argv,timeout):
            return (0,PRIVATE+"z"*300000,"")
        result=asyncio.run(reader(runner=giant).inspect_session_counts())
        self.assertEqual("SESSION_LIST_UNAVAILABLE",result.reason)
        self.assertNotIn(PRIVATE,repr(result))

    def test_tab_inventory_owner_foreign_or_adopted_blocked(self):
        for kind in ("foreign","adopted"):
            with self.subTest(kind=kind):
                c=reader(runner=transport(tabs=payload_tabs([one_tab(owner=kind)])))
                result=asyncio.run(c.inspect_owned_tabs(isolated_namespace=N))
                self.assertEqual("TAB_OWNERSHIP_UNVERIFIED",result.reason)
                self.assertFalse(result.browser_send_authorized)

    def test_duplicate_target_and_tab_id_blocked(self):
        bad=[
            [one_tab(),one_tab(tab_id="tab-2",target="target-1")],
            [one_tab(),one_tab(tab_id="tab-1",target="target-2")],
        ]
        for data in bad:
            with self.subTest(data=data):
                c=reader(runner=transport(tabs=payload_tabs(data)))
                result=asyncio.run(c.inspect_owned_tabs(isolated_namespace=N))
                self.assertEqual("TAB_OWNERSHIP_UNVERIFIED",result.reason)

    def test_invalid_session_namespace_no_cli_calls(self):
        fake=transport()
        c=reader(runner=fake)
        for name in ("master",None,"","worker-1"):
            with self.subTest(name=name):
                result=asyncio.run(c.inspect_owned_tabs(isolated_namespace=name))
                self.assertEqual("ISOLATED_READONLY_SCOPE_INVALID",result.reason)
        self.assertEqual([],fake.calls)

    def test_invalid_timeout_no_cli_calls(self):
        fake=transport()
        c=reader(runner=fake)
        for secs in (0,21,True,"10",None):
            with self.subTest(secs=secs):
                result=asyncio.run(c.inspect_session_counts(timeout_seconds=secs))
                self.assertEqual("READONLY_TIMEOUT_INVALID",result.reason)
        self.assertEqual([],fake.calls)

    def test_unknown_page_url_is_only_counted_as_other_not_output(self):
        fake=transport(tabs=payload_tabs([
            one_tab(url="https://chatgpt.com/c/"+PRIVATE),
        ]))
        result=asyncio.run(reader(runner=fake).inspect_owned_tabs(isolated_namespace=N))
        self.assertEqual(1,result.owned_tabs)
        self.assertEqual(0,result.chatgpt_home_tabs)
        self.assertEqual(0,result.blank_tabs)
        self.assertNotIn(PRIVATE,repr(result))

    def test_wrapper_exposes_no_browser_mutation_methods(self):
        c=reader()
        for method in ("run_json","fill_text","click","type","tab_new",
                       "open_new_tab","navigate","send","session_resume"):
            self.assertFalse(hasattr(c,method),method)

    def test_tab_read_uses_only_scoped_list_full_argv(self):
        fake=transport()
        c=reader(runner=fake)
        result=asyncio.run(c.inspect_owned_tabs(isolated_namespace=N))
        self.assertEqual("READONLY_COUNTS_UNATTESTED",result.status)
        self.assertEqual(1,len(fake.calls))
        self.assertEqual((
            "chrome-use.exe","--session",N,"--json","tab","list","--full",
        ),fake.calls[0])

    def test_readonly_session_list_uses_no_session_resume(self):
        fake=transport()
        c=reader(runner=fake)
        result=asyncio.run(c.inspect_session_counts())
        self.assertEqual("READONLY_COUNTS_UNATTESTED",result.status)
        self.assertEqual((
            "chrome-use.exe","--json","session","list",
        ),fake.calls[0])
        self.assertNotIn("resume",fake.calls[0])

    def test_fake_http_or_stdout_cannot_attest_terminal_event(self):
        fake=transport(tabs=payload_tabs([one_tab(url="https://chatgpt.com/")]))
        result=asyncio.run(reader(runner=fake).inspect_owned_tabs(isolated_namespace=N))
        self.assertFalse(result.terminal_event_verified)
        self.assertFalse(result.browser_send_authorized)


if __name__=="__main__":
    unittest.main()
