import asyncio
import unittest
from unittest import mock

from chrome_use_cli_v3 import ChromeUseCliV3, _default_runner


class ChromeUseCliV3Tests(unittest.TestCase):
    def test_open_new_tab_selects_the_created_tab_before_returning(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append((list(argv), timeout_seconds))
            if len(calls) == 1:
                return 0, '{"success":true,"data":{"tabId":"t2","url":"https://chatgpt.com/"}}', ''
            if len(calls) == 2:
                return 0, '{"success":true,"data":{"tabId":"t2","verified":"confirmed"}}', ''
            return 0, '{"success":true,"data":{"url":"https://chatgpt.com/"}}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        result = asyncio.run(cli.open_new_tab('scorp-p0-a', 'https://chatgpt.com/', timeout_seconds=4))

        self.assertEqual({"success": True, "data": {"tabId": "t2", "verified": "confirmed"}}, result)
        self.assertEqual(
            [
                ['--session', 'scorp-p0-a', '--json', 'tab', 'new', 'https://chatgpt.com/'],
                ['--session', 'scorp-p0-a', '--json', 'tab', 'select', 't2'],
                ['--session', 'scorp-p0-a', '--json', 'get', 'url'],
            ],
            [call[0][1:] for call in calls],
        )

    def test_open_new_tab_waits_for_selected_tab_url_after_transient_blank(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append((list(argv), timeout_seconds))
            index = len(calls)
            if index == 1:
                return 0, '{"success":true,"data":{"tabId":"t2","url":"https://chatgpt.com/"}}', ''
            if index == 2:
                return 0, '{"success":true,"data":{"tabId":"t2","verified":"confirmed"}}', ''
            if index == 3:
                return 0, '{"success":true,"data":{"url":"about:blank"}}', ''
            return 0, '{"success":true,"data":{"url":"https://chatgpt.com/"}}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        result = asyncio.run(cli.open_new_tab('scorp-p0-a', 'https://chatgpt.com/', timeout_seconds=4))

        self.assertEqual({"success": True, "data": {"tabId": "t2", "verified": "confirmed"}}, result)
        self.assertEqual(
            ['tab', 'select', 't2'],
            calls[1][0][4:],
        )
        self.assertEqual(
            ['get', 'url'],
            calls[2][0][4:],
        )
        self.assertEqual(4, len(calls))

    def test_prepare_interactive_is_background_by_default(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append((list(argv), timeout_seconds))
            return 0, '{"success":true}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        self.assertEqual(
            {"success": True, "interactive": False},
            asyncio.run(cli.prepare_interactive('scorp-p0-a', timeout_seconds=4)),
        )
        self.assertEqual([], calls)

    def test_prepare_interactive_surfaces_the_bound_session_when_enabled(self):
        calls = []

        async def runner(argv, timeout_seconds):
            calls.append((list(argv), timeout_seconds))
            return 0, '{"success":true}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner, interactive=True)
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

    def test_run_json_serializes_commands_for_one_chrome_use_daemon(self):
        active = 0
        maximum = 0

        async def runner(argv, timeout_seconds):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.03)
            active -= 1
            return 0, '{"ok":true}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)

        async def exercise():
            return await asyncio.gather(
                cli.run_json('worker-a', 'read', timeout_seconds=2),
                cli.run_json('worker-b', 'read', timeout_seconds=2),
            )

        self.assertEqual([{"ok": True}, {"ok": True}], asyncio.run(exercise()))
        self.assertEqual(1, maximum)

    def test_cancelled_mutex_wait_cannot_acquire_late_or_leak_lock(self):
        import threading
        import time

        async def runner(argv, timeout_seconds):
            return 0, '{"ok":true}', ''

        cli = ChromeUseCliV3(executable='chrome-use.exe', runner=runner)
        self.assertTrue(cli._daemon_mutex.acquire(blocking=False))
        released = threading.Event()

        def release_original_holder():
            time.sleep(0.05)
            cli._daemon_mutex.release()
            released.set()

        thread = threading.Thread(target=release_original_holder)
        thread.start()

        async def exercise():
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    cli.run_json('worker-cancelled', 'read', timeout_seconds=0.5),
                    timeout=0.01,
                )

        started = time.monotonic()
        asyncio.run(exercise())
        elapsed = time.monotonic() - started
        thread.join(timeout=1.0)
        self.assertTrue(released.is_set())
        self.assertLess(elapsed, 0.25)
        self.assertTrue(cli._daemon_mutex.acquire(blocking=False))
        cli._daemon_mutex.release()

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
