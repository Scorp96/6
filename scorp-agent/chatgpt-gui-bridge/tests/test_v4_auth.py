import pathlib
import unittest
import asyncio

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from v4_auth import classify_chatgpt_snapshot, probe_chatgpt_auth


class ChatGptAuthClassificationTests(unittest.TestCase):
    def test_rate_limit_wins_even_when_plus_marker_is_present(self):
        snapshot = 'ChatGPT Plus\n请求过于频繁\n为保障数据安全，我们已暂时限制你访问对话记录。请稍等几分钟后再重试。'
        result = classify_chatgpt_snapshot(snapshot, 'worker-1')
        self.assertEqual(result['status'], 'BROWSER_RATE_LIMITED')
        self.assertEqual(result['channel'], 'worker-1')

    def test_login_and_captcha_are_blocked(self):
        for snapshot, expected in [
            ('Log in to ChatGPT', 'AUTHENTICATION_REQUIRED'),
            ('验证码 required', 'CAPTCHA_REQUIRED'),
        ]:
            with self.subTest(snapshot=snapshot):
                self.assertEqual(classify_chatgpt_snapshot(snapshot, 'master')['status'], expected)

    def test_ready_authenticated_page_is_accepted(self):
        result = classify_chatgpt_snapshot('ChatGPT Plus\nReady', 'master')
        self.assertEqual(result, {'status': 'AUTHENTICATED', 'channel': 'master'})

    def test_rate_limit_probe_acknowledges_then_rechecks_without_submitting(self):
        class Cli:
            def __init__(self):
                self.reads = 0
                self.calls = []

            async def run_json(self, session, *args, timeout_seconds=30):
                self.calls.append((session, list(args)))
                if args[0] != 'read':
                    raise AssertionError(args)
                self.reads += 1
                return {'data': 'ChatGPT Plus\\nReady'}

        class Driver:
            def __init__(self):
                self.calls = []

            async def recover_rate_limit_dialog(self, session, **kwargs):
                self.calls.append((session, kwargs))
                return {'status': 'RECOVERED', 'reason': 'RATE_LIMIT_DIALOG_CLEARED'}

        cli = Cli()
        # The first read is represented as throttled by the probe helper's
        # injected classifier, while the second read is authenticated.
        original = classify_chatgpt_snapshot
        seen = []

        def classify_once(snapshot, channel):
            seen.append(snapshot)
            if len(seen) == 1:
                return {'status': 'BROWSER_RATE_LIMITED', 'channel': channel}
            return original(snapshot, channel)

        result = asyncio.run(probe_chatgpt_auth(
            cli,
            Driver(),
            'session-1',
            'worker-1',
            classify_fn=classify_once,
        ))
        self.assertEqual('AUTHENTICATED', result['status'])
        self.assertEqual('RECOVERED', result['rate_limit_recovery']['status'])
        self.assertEqual(2, cli.reads)


if __name__ == '__main__':
    unittest.main()
