import asyncio
import pathlib
import tempfile
import unittest

from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3


class FakeCli:
    def __init__(self):
        self.calls = []
        self.responses = []

    async def run_json(self, session, *args, timeout_seconds=30):
        self.calls.append((session, list(args), timeout_seconds))
        if not self.responses:
            raise AssertionError(f"unexpected call: {session} {args}")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class ChromeUseActorDriverV3Tests(unittest.TestCase):
    def _driver(self, td, cli):
        return ChromeUseActorDriverV3(cli, pathlib.Path(td) / 'chrome-use-driver-v3.json', sleeper=lambda _: asyncio.sleep(0))

    def test_same_conversation_keeps_same_session_across_logical_turns(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            driver = self._driver(td, cli)
            url = 'https://chatgpt.com/c/persistent-worker-slot'
            first = driver.bind_turn('turn-alpha', url)
            second = driver.bind_turn('turn-beta', url)
            self.assertEqual(first, second)
            self.assertTrue(first.startswith('scorp-p0-conv-'))

    def test_snapshot_fails_closed_when_exact_url_cannot_be_reacquired(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            target = 'https://chatgpt.com/c/target-one'
            cli.responses = [
                {'data': {'value': 'https://chatgpt.com/c/wrong-one'}},
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/c/still-wrong'}},
            ]
            driver = self._driver(td, cli)
            with self.assertRaisesRegex(ValueError, 'ACTOR_GUI_FOCUSED_CONVERSATION_MISMATCH'):
                asyncio.run(driver.snapshot_conversation(target))
            self.assertFalse(any(args and args[0] == 'snapshot' for _, args, _ in cli.calls))

    def test_snapshot_reacquires_target_from_blank_new_session(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            target = 'https://chatgpt.com/c/legacy-worker'
            cli.responses = [
                {'data': {'url': 'about:blank'}},
                {'success': True},
                {'data': {'url': target}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'SCORP_GUI_ACTOR_V3::legacy-turn::TOKEN'}},
            ]
            driver = self._driver(td, cli)
            text = asyncio.run(driver.snapshot_conversation(target))
            self.assertIn(target, text)
            self.assertIn('SCORP_GUI_ACTOR_V3::legacy-turn::TOKEN', text)
            self.assertEqual(['get', 'url'], cli.calls[0][1])
            self.assertEqual(['open', target], cli.calls[1][1])
            self.assertEqual(['get', 'url'], cli.calls[2][1])
            self.assertEqual(['bringToFront'], cli.calls[3][1])
            self.assertEqual(['read'], cli.calls[4][1])

    def test_snapshot_contains_verified_url_and_remote_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            target = 'https://chatgpt.com/c/target-two'
            cli.responses = [
                {'data': {'value': target}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'SCORP_GUI_ACTOR_V3::turn-2::TOKEN'}},
            ]
            driver = self._driver(td, cli)
            text = asyncio.run(driver.snapshot_conversation(target))
            self.assertIn(target, text)
            self.assertIn('SCORP_GUI_ACTOR_V3::turn-2::TOKEN', text)
            self.assertEqual(['get', 'url'], cli.calls[0][1])
            self.assertEqual(['bringToFront'], cli.calls[1][1])
            self.assertEqual(['read'], cli.calls[2][1])

    def test_submit_uses_unique_accessibility_textbox_ref_not_css_id(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            conversation = 'https://chatgpt.com/c/live-ref-test'
            cli.responses = [
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/'}},
                {
                    'data': {
                        'refs': {
                            'e10': {'name': '添加文件等', 'role': 'button'},
                            'e11': {'name': '与 ChatGPT 聊天', 'role': 'textbox'},
                        },
                        'snapshot': '- textbox "与 ChatGPT 聊天" [ref=e11]',
                    }
                },
                {'success': True},
                {
                    'data': {
                        'refs': {
                            'e11': {'name': '与 ChatGPT 聊天', 'role': 'textbox'},
                            'e20': {'name': '发送提示词', 'role': 'button'},
                        },
                        'snapshot': '- textbox "与 ChatGPT 聊天" [ref=e11]: TURN_ID=turn-ref\nhello\n- button "发送提示词" [ref=e20]',
                    }
                },
                {'success': True},
                {'data': {'value': conversation}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'submitted'}},
            ]
            driver = self._driver(td, cli)
            asyncio.run(
                driver.submit_prompt(
                    prompt='TURN_ID=turn-ref\nhello',
                    turn_id='turn-ref',
                    actor_kind='WORKER',
                    conversation_url=None,
                )
            )
            fill_calls = [args for _, args, _ in cli.calls if args and args[0] == 'fill']
            click_calls = [args for _, args, _ in cli.calls if args and args[0] == 'click']
            press_calls = [args for _, args, _ in cli.calls if args and args[0] == 'press']
            self.assertEqual([['fill', '@e11', 'TURN_ID=turn-ref\nhello']], fill_calls)
            self.assertEqual([['click', '@e20']], click_calls)
            self.assertEqual([], press_calls)
            self.assertFalse(any('#prompt-textarea' in args for _, args, _ in cli.calls))

    def test_submit_accepts_chatgpt_root_query_redirect_before_new_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            conversation = 'https://chatgpt.com/c/live-root-query'
            cli.responses = [
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/?oai-dm=1'}},
                {
                    'data': {
                        'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}},
                        'snapshot': '- textbox "Message ChatGPT" [ref=e11]',
                    }
                },
                {'success': True},
                {
                    'data': {
                        'refs': {
                            'e11': {'name': 'Message ChatGPT', 'role': 'textbox'},
                            'e20': {'name': 'Send', 'role': 'button'},
                        },
                        'snapshot': '- textbox "Message ChatGPT" [ref=e11]\n- button "Send" [ref=e20]',
                    }
                },
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/c/WEB:transient'}},
                {'data': {'value': conversation}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'submitted'}},
            ]
            driver = self._driver(td, cli)
            result = asyncio.run(driver.submit_prompt(
                prompt='harmless', turn_id='turn-root-query', actor_kind='WORKER', conversation_url=None
            ))
            self.assertIn(conversation, result)

    def test_failed_submit_still_persists_turn_session_for_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / 'chrome-use-driver-v3.json'
            cli = FakeCli()
            cli.responses = [
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/'}},
                {
                    'data': {
                        'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}},
                        'snapshot': '- textbox "Message ChatGPT" [ref=e11]',
                    }
                },
                RuntimeError('SIMULATED_FILL_CRASH'),
            ]
            driver = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))
            with self.assertRaisesRegex(RuntimeError, 'SIMULATED_FILL_CRASH'):
                asyncio.run(driver.submit_prompt(prompt='TURN_ID=turn-crash\nhello', turn_id='turn-crash', actor_kind='WORKER', conversation_url=None))
            reloaded = ChromeUseActorDriverV3(FakeCli(), state_path, sleeper=lambda _: asyncio.sleep(0))
            binding = reloaded.turn_binding('turn-crash')
            self.assertIsNotNone(binding)
            self.assertTrue(binding['session'].startswith('scorp-p0-turn-'))

    def test_recovery_reads_persisted_session_without_submitting(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / 'chrome-use-driver-v3.json'
            seed = FakeCli()
            driver = ChromeUseActorDriverV3(seed, state_path, sleeper=lambda _: asyncio.sleep(0))
            session = driver.bind_turn('turn-recover', None)
            cli = FakeCli()
            url = 'https://chatgpt.com/c/recover-one'
            cli.responses = [
                {'data': {'value': url}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'TURN_ID=turn-recover\nSCORP_GUI_ACTOR_V3::turn-recover::TOKEN'}},
            ]
            reloaded = ChromeUseActorDriverV3(cli, state_path, sleeper=lambda _: asyncio.sleep(0))
            found = asyncio.run(reloaded.discover_turn_conversations('turn-recover'))
            self.assertEqual(1, len(found))
            self.assertEqual(url, found[0]['conversation_url'])
            self.assertEqual(session, cli.calls[0][0])
            self.assertFalse(any(args and args[0] in {'fill', 'press', 'type', 'click'} for _, args, _ in cli.calls))


if __name__ == '__main__':
    unittest.main()
