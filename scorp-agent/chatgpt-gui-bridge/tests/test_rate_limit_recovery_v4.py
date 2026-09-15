import asyncio
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3


class FakeCli:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def run_json(self, session, *args, timeout_seconds=30):
        self.calls.append((session, list(args), timeout_seconds))
        if not self.responses:
            raise AssertionError(f"unexpected call: {session} {args}")
        return self.responses.pop(0)


class FailingCli(FakeCli):
    def __init__(self, responses, *, fail_on):
        super().__init__(responses)
        self.fail_on = list(fail_on)

    async def run_json(self, session, *args, timeout_seconds=30):
        if list(args) == self.fail_on:
            self.calls.append((session, list(args), timeout_seconds))
            raise RuntimeError("simulated chrome-use failure")
        return await super().run_json(session, *args, timeout_seconds=timeout_seconds)


def snapshot(text, refs=None):
    return {"data": {"refs": refs or {}, "snapshot": text}}


class RateLimitRecoveryV4Tests(unittest.TestCase):
    def test_ack_failure_fails_closed_without_retrying_or_raising(self):
        cli = FailingCli(
            [snapshot(
                "请求过于频繁，请稍等几分钟后再重试",
                {"e42": {"name": "确定", "role": "button"}},
            )],
            fail_on=["click", "@e42"],
        )
        with tempfile.TemporaryDirectory() as td:
            driver = ChromeUseActorDriverV3(cli, pathlib.Path(td) / "state.json")
            result = asyncio.run(driver.recover_rate_limit_dialog("worker-session", max_wait_seconds=0))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("RATE_LIMIT_ACK_FAILED", result["reason"])
        self.assertEqual([["snapshot", "-i"], ["click", "@e42"]], [args for _, args, _ in cli.calls])

    def test_recovery_read_failure_fails_closed_without_refresh_or_retry(self):
        cli = FailingCli(
            [
                snapshot(
                    "请求过于频繁，请稍等几分钟后再重试",
                    {"e42": {"name": "确定", "role": "button"}},
                ),
                {"success": True},
            ],
            fail_on=["snapshot", "-i"],
        )
        # Fail only the second snapshot: the initial preflight must still be read.
        calls = {"snapshot": 0}

        async def fail_after_initial(session, *args, timeout_seconds=30):
            if list(args[:1]) == ["snapshot"]:
                calls["snapshot"] += 1
                if calls["snapshot"] == 2:
                    raise RuntimeError("simulated snapshot failure")
            return await FakeCli.run_json(cli, session, *args, timeout_seconds=timeout_seconds)

        cli.run_json = fail_after_initial
        with tempfile.TemporaryDirectory() as td:
            driver = ChromeUseActorDriverV3(cli, pathlib.Path(td) / "state.json")
            result = asyncio.run(driver.recover_rate_limit_dialog("worker-session", max_wait_seconds=0))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("RATE_LIMIT_RECOVERY_READ_FAILED", result["reason"])
        self.assertEqual(0, len([args for _, args, _ in cli.calls if args[:1] == ["reload"]]))

    def test_acknowledges_known_dialog_once_then_waits_for_authenticated_page(self):
        cli = FakeCli([
            snapshot(
                "请求过于频繁，请稍等几分钟后再重试",
                {"e42": {"name": "确定", "role": "button"}},
            ),
            {"success": True},
            snapshot("ChatGPT Plus\nReady"),
        ])
        with tempfile.TemporaryDirectory() as td:
            driver = ChromeUseActorDriverV3(cli, pathlib.Path(td) / "state.json")
            result = asyncio.run(driver.recover_rate_limit_dialog("worker-session", max_wait_seconds=0))
        self.assertEqual("RECOVERED", result["status"])
        self.assertEqual(
            [["snapshot", "-i"], ["click", "@e42"], ["snapshot", "-i"]],
            [args for _, args, _ in cli.calls],
        )

    def test_unknown_dialog_button_fails_closed_without_clicking(self):
        cli = FakeCli([
            snapshot(
                "请求过于频繁，请稍等几分钟后再重试",
                {"e42": {"name": "继续", "role": "button"}},
            ),
        ])
        with tempfile.TemporaryDirectory() as td:
            driver = ChromeUseActorDriverV3(cli, pathlib.Path(td) / "state.json")
            result = asyncio.run(driver.recover_rate_limit_dialog("worker-session", max_wait_seconds=0))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("RATE_LIMIT_ACK_UNRESOLVED", result["reason"])
        self.assertEqual([["snapshot", "-i"]], [args for _, args, _ in cli.calls])

    def test_timeout_does_not_retry_ack_or_submit(self):
        cli = FakeCli([
            snapshot(
                "请求过于频繁，请稍等几分钟后再重试",
                {"e42": {"name": "确定", "role": "button"}},
            ),
            {"success": True},
            snapshot("请求过于频繁，请稍等几分钟后再重试"),
            snapshot("请求过于频繁，请稍等几分钟后再重试"),
            {"success": True},
            snapshot("请求过于频繁，请稍等几分钟后再重试"),
        ])
        ticks = iter([0.0, 0.0, 6.0])
        with tempfile.TemporaryDirectory() as td:
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "state.json",
                clock=lambda: next(ticks),
                sleeper=lambda _: asyncio.sleep(0),
            )
            result = asyncio.run(driver.recover_rate_limit_dialog("worker-session", max_wait_seconds=5, poll_seconds=5))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("RATE_LIMIT_RECOVERY_TIMEOUT", result["reason"])
        self.assertEqual(1, len([args for _, args, _ in cli.calls if args[:1] == ["click"]]))
        self.assertEqual(1, len([args for _, args, _ in cli.calls if args[:1] == ["reload"]]))

    def test_wait_window_then_refreshes_once_before_final_read_only_check(self):
        cli = FakeCli([
            snapshot(
                "请求过于频繁，请稍等几分钟后再重试",
                {"e42": {"name": "确定", "role": "button"}},
            ),
            {"success": True},
            snapshot("请求过于频繁，请稍等几分钟后再重试"),
            {"success": True},
            snapshot("ChatGPT Plus\nReady"),
        ])
        ticks = iter([0.0, 6.0])
        with tempfile.TemporaryDirectory() as td:
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / "state.json",
                clock=lambda: next(ticks),
                sleeper=lambda _: asyncio.sleep(0),
            )
            result = asyncio.run(
                driver.recover_rate_limit_dialog(
                    "worker-session",
                    max_wait_seconds=5,
                    poll_seconds=5,
                )
            )
        self.assertEqual("RECOVERED", result["status"])
        self.assertEqual(
            [["snapshot", "-i"], ["click", "@e42"], ["snapshot", "-i"], ["reload"], ["snapshot", "-i"]],
            [args for _, args, _ in cli.calls],
        )


if __name__ == "__main__":
    unittest.main()
