import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from v4_auth import classify_chatgpt_snapshot


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


if __name__ == '__main__':
    unittest.main()
