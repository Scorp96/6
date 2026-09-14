import asyncio
import unittest
from unittest import mock

from chrome_use_cli_v3 import ChromeUseCliV3, _default_runner


class ChromeUseCliV3Tests(unittest.TestCase):
    def test_prepare_interactive_surfaces_the_bound_session(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append((list(argv), timeout_seconds))
            return 0, '{"success":true}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        self.assertEqual(
            {"success": True},
            asyncio.run(cli.prepare_interactive('scorp-p0-a', timeout_seconds=4)),
        )
        self.assertEqual(
            ['--session', 'scorp-p0-a', '--json', 'bringToFront'],
            calls[0][0][1:],
        )
        self.assertEqual(4, calls[0][1])

    def test_run_json_binds_explicit_session_and_parses_json(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append((list(argv), timeout_seconds))
            return 0, '{"ok":true}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        result = asyncio.run(cli.run_json('scorp-p0-a', 'tab', 'list', timeout_seconds=9))
        self.assertEqual({"ok": True}, result)
        self.assertEqual(9, calls[0][1])
        self.assertEqual('chrome-use.exe', calls[0][0][0])
        self.assertEqual(['--session', 'scorp-p0-a', '--json', 'tab', 'list'], calls[0][0][1:])

    def test_run_json_rejects_invalid_json_even_on_zero_exit(self):
        async def runner(argv, timeout_seconds):
            return 0, 'not-json', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        with self.assertRaisesRegex(ValueError, 'CHROME_USE_INVALID_JSON'):
            asyncio.run(cli.run_json('scorp-p0-a', 'status', timeout_seconds=9))

    def test_run_json_reports_nonzero_exit_without_retrying(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append(list(argv))
            return 7, '', 'busy'

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        with self.assertRaisesRegex(RuntimeError, 'CHROME_USE_EXIT_7'):
            asyncio.run(cli.run_json('scorp-p0-a', 'status', timeout_seconds=9))
        self.assertEqual(1, len(calls))

    def test_default_runner_timeout_returns_even_when_child_keeps_pipes_open(self):
        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self.pid = 12345
                self.killed = False

            async def communicate(self):
                await asyncio.sleep(10)
                return b'', b''

            async def wait(self):
                if not self.killed:
                    await asyncio.sleep(10)
                self.returncode = 9
                return self.returncode

            def kill(self):
                self.killed = True

        proc = FakeProcess()

        async def fake_create(*args, **kwargs):
            return proc

        async def exercise():
            with mock.patch('chrome_use_cli_v3.asyncio.create_subprocess_exec', new=fake_create):
                with self.assertRaisesRegex(TimeoutError, 'CHROME_USE_TIMEOUT'):
                    await asyncio.wait_for(
                        _default_runner(['chrome-use.exe', '--session', 'fresh', '--json', 'get', 'url'], 0.01),
                        timeout=0.5,
                    )

        asyncio.run(exercise())
        self.assertTrue(proc.killed)

    def test_default_runner_outer_cancellation_kills_child_before_propagating(self):
        class FakeProcess:
            def __init__(self):
                self.returncode = None
                self.killed = False
                self.wait_calls = 0

            async def wait(self):
                self.wait_calls += 1
                if not self.killed:
                    await asyncio.sleep(10)
                self.returncode = 9
                return self.returncode

            def kill(self):
                self.killed = True

        proc = FakeProcess()

        async def fake_create(*args, **kwargs):
            return proc

        async def exercise():
            with mock.patch('chrome_use_cli_v3.asyncio.create_subprocess_exec', new=fake_create):
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        _default_runner(['chrome-use.exe', '--session', 'fresh', '--json', 'read'], 10),
                        timeout=0.01,
                    )

        asyncio.run(exercise())
        self.assertTrue(proc.killed)
        self.assertGreaterEqual(proc.wait_calls, 2)


if __name__ == '__main__':
    unittest.main()
