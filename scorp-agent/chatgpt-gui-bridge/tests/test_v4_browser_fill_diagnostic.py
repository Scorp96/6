from __future__ import annotations

import asyncio
import pathlib
import tempfile
import unittest

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
from tools.v4_browser_fill_diagnostic import (
    run_fill_diagnostic,
    validate_candidate_binding,
)


class FakeCli:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def run_json(self, session, *args, timeout_seconds=30):
        self.calls.append((session, list(args), timeout_seconds))
        if not self.responses:
            raise AssertionError(f"unexpected call: {session} {args}")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class V4BrowserFillDiagnosticTests(unittest.TestCase):
    def test_candidate_binding_requires_exact_commit_and_manifest_hash(self):
        manifest = {
            "candidate_commit": "a" * 40,
            "manifest_sha256": "b" * 64,
        }
        self.assertEqual(
            manifest,
            validate_candidate_binding(manifest, "a" * 40, "b" * 64),
        )
        with self.assertRaisesRegex(ValueError, "DIAGNOSTIC_CANDIDATE_MISMATCH"):
            validate_candidate_binding(manifest, "c" * 40, "b" * 64)
        with self.assertRaisesRegex(ValueError, "DIAGNOSTIC_MANIFEST_HASH_INVALID"):
            validate_candidate_binding(manifest, "a" * 40, "not-a-hash")

    def test_fill_diagnostic_never_clicks_and_records_ready_send_control(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli([
                {"success": True},
                {"data": {"value": "https://chatgpt.com/"}},
                {"data": {"refs": {"e11": {"name": "Message ChatGPT", "role": "textbox"}}}},
                {"success": True},
                {"data": {"refs": {
                    "e11": {"name": "Message ChatGPT", "role": "textbox"},
                    "e20": {"name": "Send", "role": "button"},
                }}},
                {"success": True},
            ])
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "driver.json",
                sleeper=lambda _: asyncio.sleep(0),
            )
            evidence = asyncio.run(run_fill_diagnostic(
                cli,
                driver,
                session="fill-diagnostic",
                turn_id="fill-diagnostic-turn",
                marker="SCORP_FILL_ONLY_MARKER",
                cleanup=True,
            ))
            self.assertEqual("READY_TO_SEND_NO_CLICK", evidence["status"])
            self.assertEqual("@e20", evidence["send_ref"])
            self.assertEqual(0, evidence["submit_actions"])
            self.assertFalse(any(args and args[0] == "click" for _, args, _ in cli.calls))
            self.assertEqual(["session", "stop"], cli.calls[-1][1])

    def test_fill_diagnostic_uses_key_event_repair_and_still_never_clicks(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli([
                {"success": True},
                {"data": {"value": "https://chatgpt.com/"}},
                {"data": {"refs": {"e11": {"name": "Message ChatGPT", "role": "textbox"}}}},
                {"success": True},
                {"data": {"refs": {"e11": {"name": "Message ChatGPT", "role": "textbox"}}}},
                {"success": True},
                {"data": {"refs": {
                    "e11": {"name": "Message ChatGPT", "role": "textbox"},
                    "e20": {"name": "Send", "role": "button"},
                }}},
                {"success": True},
            ])
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "driver.json",
                sleeper=lambda _: asyncio.sleep(0),
            )
            evidence = asyncio.run(run_fill_diagnostic(
                cli,
                driver,
                session="fill-repair",
                turn_id="fill-repair-turn",
                marker="SCORP_FILL_ONLY_MARKER",
                cleanup=True,
            ))
            self.assertEqual("READY_TO_SEND_NO_CLICK", evidence["status"])
            self.assertEqual("USED", evidence["key_event_repair"])
            self.assertEqual(
                ["type", "@e11", "SCORP_FILL_ONLY_MARKER", "--key-events", "--clear"],
                [args for _, args, _ in cli.calls if args and args[0] == "type"][0],
            )
            self.assertFalse(any(args and args[0] == "click" for _, args, _ in cli.calls))


if __name__ == "__main__":
    unittest.main()
