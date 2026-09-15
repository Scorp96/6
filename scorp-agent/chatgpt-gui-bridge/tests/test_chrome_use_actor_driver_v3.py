import asyncio
import concurrent.futures
import json
import pathlib
import tempfile
import time
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


class NewTabFakeCli(FakeCli):
    async def open_new_tab(self, session, url, *, timeout_seconds=30):
        self.calls.append((session, ['tab', 'new', url], timeout_seconds))
        return {'success': True, 'data': {'url': url}}


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

    def test_lifecycle_records_roles_and_migrates_legacy_state(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / 'chrome-use-driver-v3.json'
            state_path.write_text(json.dumps({
                'protocol_version': 'scorp.chrome-use-driver/v1',
                'turns': {},
                'conversations': {},
            }), encoding='utf-8')
            driver = self._driver(td, FakeCli())
            session = driver.bind_turn('master-turn', None, actor_kind='MASTER')
            snapshot = driver.lifecycle_snapshot()
            self.assertEqual('MASTER', snapshot[session]['role'])
            self.assertEqual('ACTIVE', snapshot[session]['status'])
            self.assertEqual(['master-turn'], snapshot[session]['turn_ids'])
            persisted = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertIn('sessions', persisted)

    def test_retire_diagnostic_stops_only_explicit_temporary_session_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [{'success': True}]
            driver = self._driver(td, cli)
            driver.register_session('diag-123', role='DIAGNOSTIC')
            first = asyncio.run(driver.retire_session('diag-123', reason='diagnostic complete', stop=True))
            second = asyncio.run(driver.retire_session('diag-123', reason='repeat cleanup', stop=True))
            self.assertEqual('STOPPED', first['cleanup'])
            self.assertEqual('ALREADY_RETIRED', second['status'])
            self.assertEqual([['session', 'stop']], [args for _, args, _ in cli.calls])
            self.assertEqual('RETIRED', driver.lifecycle_snapshot()['diag-123']['status'])

    def test_retire_refuses_active_persistent_master_or_worker(self):
        with tempfile.TemporaryDirectory() as td:
            driver = self._driver(td, FakeCli())
            master = driver.bind_turn('master-turn', None, actor_kind='MASTER')
            with self.assertRaisesRegex(ValueError, 'PERSISTENT_SESSION_REFUSED'):
                asyncio.run(driver.retire_session(master, reason='unsafe cleanup', stop=True))
            self.assertEqual('ACTIVE', driver.lifecycle_snapshot()[master]['status'])

    def test_retire_turn_does_not_stop_shared_session_until_all_turns_retire(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [{'success': True}]
            driver = self._driver(td, cli)
            url = 'https://chatgpt.com/c/shared-worker'
            session = driver.bind_turn('worker-turn-1', url, actor_kind='WORKER')
            self.assertEqual(session, driver.bind_turn('worker-turn-2', url, actor_kind='WORKER'))
            first = asyncio.run(driver.retire_turn('worker-turn-1', reason='assignment complete', stop=True, allow_persistent=True))
            self.assertEqual('NOT_REQUESTED', first['cleanup'])
            self.assertEqual([], cli.calls)
            second = asyncio.run(driver.retire_turn('worker-turn-2', reason='assignment complete', stop=True, allow_persistent=True))
            self.assertEqual('STOPPED', second['cleanup'])
            self.assertEqual([['session', 'stop']], [args for _, args, _ in cli.calls])

    def test_retiring_worker_assignment_keeps_persistent_slot_reusable(self):
        with tempfile.TemporaryDirectory() as td:
            driver = self._driver(td, FakeCli())
            url = 'https://chatgpt.com/c/reusable-worker'
            session = driver.bind_turn('worker-turn-old', url, actor_kind='WORKER')
            result = asyncio.run(driver.retire_turn('worker-turn-old', reason='slot released'))
            self.assertEqual('NOT_REQUESTED', result['cleanup'])
            self.assertEqual('ACTIVE', driver.lifecycle_snapshot()[session]['status'])
            self.assertEqual(session, driver.bind_turn('worker-turn-new', url, actor_kind='WORKER'))

    def test_cleanup_timeout_is_recorded_as_blocked_without_retry(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [TimeoutError('stuck chrome-use get url')]
            driver = self._driver(td, cli)
            driver.register_session('diag-timeout', role='DIAGNOSTIC')
            result = asyncio.run(driver.retire_session('diag-timeout', reason='cleanup probe', stop=True))
            self.assertEqual('CLEANUP_BLOCKED', result['cleanup'])
            self.assertEqual('CLEANUP_BLOCKED', driver.lifecycle_snapshot()['diag-timeout']['cleanup_status'])
            self.assertEqual(1, len(cli.calls))

    def test_parallel_turn_bindings_preserve_both_sessions_in_shared_state(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / 'chrome-use-driver-v3.json'
            driver = self._driver(td, FakeCli())
            original_load = driver._load

            def slow_load():
                value = original_load()
                time.sleep(0.03)
                return value

            driver._load = slow_load
            turns = ('parallel-turn-one', 'parallel-turn-two')
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                sessions = list(pool.map(lambda turn: driver.bind_turn(turn, None), turns))

            stored = json.loads(state_path.read_text(encoding='utf-8'))
            self.assertEqual(set(turns), set(stored['turns']))
            self.assertEqual(2, len(set(sessions)))

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

    def test_submit_recovers_worker_rate_limit_before_filling_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            cli = NewTabFakeCli()
            conversation = 'https://chatgpt.com/c/rate-limit-recovered'
            cli.responses = [
                {'data': {'value': 'https://chatgpt.com/'}},
                {
                    'data': {
                        'refs': {'e42': {'name': '确定', 'role': 'button'}},
                        'snapshot': '请求过于频繁，请稍等几分钟后再重试',
                    }
                },
                {
                    'data': {
                        'refs': {'e42': {'name': '确定', 'role': 'button'}},
                        'snapshot': '请求过于频繁，请稍等几分钟后再重试',
                    }
                },
                {'success': True},
                {'data': {'snapshot': 'ChatGPT Plus\nReady'}},
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {'data': {'refs': {'e20': {'name': 'Send', 'role': 'button'}}}},
                {'success': True},
                {'data': {'value': conversation}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'submitted'}},
            ]
            driver = ChromeUseActorDriverV3(
                cli,
                pathlib.Path(td) / 'chrome-use-driver-v3.json',
                sleeper=lambda _: asyncio.sleep(0),
                clock=lambda: 0.0,
            )
            original_recovery = driver.recover_rate_limit_dialog
            recovery_called = []

            async def bounded_recovery(session, **kwargs):
                recovery_called.append(session)
                kwargs['max_wait_seconds'] = 0
                return await original_recovery(session, **kwargs)

            driver.recover_rate_limit_dialog = bounded_recovery
            result = asyncio.run(driver.submit_prompt(
                prompt='worker prompt',
                turn_id='turn-rate-limit-worker',
                actor_kind='WORKER',
                conversation_url=None,
            ))
            self.assertIn(conversation, result)
            calls = [args for _, args, _ in cli.calls]
            self.assertEqual(['click', '@e42'], calls[4])
            self.assertEqual(1, len([args for args in calls if args[:1] == ['click'] and args[1:] == ['@e42']]))
            self.assertEqual(1, len([args for args in calls if args[:1] == ['fill']]))
            self.assertEqual(1, len(recovery_called))

    def test_submit_recovers_rate_limit_after_fill_before_send_without_key_event_retry(self):
        with tempfile.TemporaryDirectory() as td:
            cli = NewTabFakeCli()
            conversation = 'https://chatgpt.com/c/rate-limit-after-fill'
            cli.responses = [
                {'data': {'value': 'https://chatgpt.com/'}},
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {
                    'data': {
                        'refs': {'e42': {'name': '确定', 'role': 'button'}},
                        'snapshot': '请求过于频繁，请稍等几分钟后再重试',
                    }
                },
                {
                    'data': {
                        'refs': {'e42': {'name': '确定', 'role': 'button'}},
                        'snapshot': '请求过于频繁，请稍等几分钟后再重试',
                    }
                },
                {'success': True},
                {'data': {'snapshot': 'ChatGPT Plus\nReady'}},
                {'data': {'refs': {'e12': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {'data': {'refs': {'e20': {'name': 'Send', 'role': 'button'}}}},
                {'success': True},
                {'data': {'value': conversation}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'submitted'}},
            ]
            driver = self._driver(td, cli)
            original_recovery = driver.recover_rate_limit_dialog

            async def bounded_recovery(session, **kwargs):
                kwargs['max_wait_seconds'] = 0
                return await original_recovery(session, **kwargs)

            driver.recover_rate_limit_dialog = bounded_recovery
            result = asyncio.run(driver.submit_prompt(
                prompt='worker prompt',
                turn_id='turn-rate-limit-after-fill',
                actor_kind='WORKER',
                conversation_url=None,
            ))
            self.assertIn(conversation, result)
            calls = [args for _, args, _ in cli.calls]
            self.assertEqual(1, len([args for args in calls if args[:1] == ['click'] and args[1:] == ['@e42']]))
            self.assertEqual(2, len([args for args in calls if args[:1] == ['fill']]))
            self.assertFalse(any(args[:1] == ['type'] and '--key-events' in args for args in calls))

    def test_missing_send_control_reports_safe_post_fill_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [{
                'data': {
                    'refs': {
                        'e11': {'name': 'Message ChatGPT', 'role': 'textbox'},
                        'e21': {'name': 'Stop generating', 'role': 'button'},
                    },
                    'snapshot': '- textbox "Message ChatGPT" [ref=e11]\n- button "Stop generating" [ref=e21]',
                }
            }]
            driver = self._driver(td, cli)
            with self.assertRaisesRegex(ValueError, 'CHROME_USE_SEND_REF_COUNT_0') as raised:
                asyncio.run(driver._send_ref('session-diagnostic'))
            self.assertEqual(['Stop generating'], raised.exception.diagnostics['button_names'])
            self.assertEqual(64, len(raised.exception.diagnostics['snapshot_sha256']))

    def test_key_event_repair_uses_editor_ref_from_fresh_failed_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [
                {
                    'data': {
                        'refs': {
                            'e12': {'name': 'Message ChatGPT', 'role': 'textbox'},
                        },
                        'snapshot': '- textbox "Message ChatGPT" [ref=e12]',
                    }
                },
                {'success': True},
                {
                    'data': {
                        'refs': {
                            'e13': {'name': 'Message ChatGPT', 'role': 'textbox'},
                            'e20': {'name': 'Send', 'role': 'button'},
                        },
                        'snapshot': '- textbox "Message ChatGPT" [ref=e13]\n- button "Send" [ref=e20]',
                    }
                },
            ]
            driver = self._driver(td, cli)
            self.assertEqual(
                '@e20',
                asyncio.run(driver._send_ref_after_input_repair('session-ref-remint', '@e11', 'hello')),
            )
            repair_calls = [
                args for _, args, _ in cli.calls
                if args and args[0] == 'type' and '--key-events' in args
            ]
            self.assertEqual([['type', '@e12', 'hello', '--key-events', '--clear']], repair_calls)

    def test_busy_fill_reconciles_composer_before_one_safe_fill_retry(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [
                RuntimeError(
                    'CHROME_USE_EXIT_1: Invalid response: EOF while parsing a value '
                    'after 5 retries - daemon may be busy or unresponsive'
                ),
                {'success': True},
                {'data': {'snapshot': 'Focused Window: Chrome\nhttps://chatgpt.com/\nempty composer'}},
                {'data': {'refs': {'e31': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
            ]
            driver = self._driver(td, cli)
            fresh_ref = asyncio.run(driver._fill_prompt_with_reconciliation(
                'session-busy-fill',
                '@e11',
                'SCORP_BUSY_FILL_MARKER',
                'https://chatgpt.com/',
            ))
            self.assertEqual('@e31', fresh_ref)
            fill_calls = [args for _, args, _ in cli.calls if args and args[0] == 'fill']
            self.assertEqual(
                [
                    ['fill', '@e11', 'SCORP_BUSY_FILL_MARKER'],
                    ['fill', '@e31', 'SCORP_BUSY_FILL_MARKER'],
                ],
                fill_calls,
            )

    def test_send_control_accepts_accessibility_aliases_and_shortcut_suffix(self):
        from chrome_use_actor_driver_v3 import _send_ref_from_snapshot

        self.assertEqual(
            '@e20',
            _send_ref_from_snapshot({
                'data': {
                    'refs': {
                        'e20': {'aria-label': 'Send message (Ctrl+Enter)', 'role': 'button'},
                    }
                }
            }),
        )
        self.assertEqual(
            '@e21',
            _send_ref_from_snapshot({
                'data': {
                    'refs': {
                        'e21': {'label': '发送提示', 'role': 'button'},
                    }
                }
            }),
        )

    def test_submit_send_diagnostic_binds_to_post_fill_editor_and_prompt_hash(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/'}},
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {'data': {'refs': {'e21': {'name': 'Stop generating', 'role': 'button'}}}},
            ]
            driver = self._driver(td, cli)
            with self.assertRaisesRegex(ValueError, 'CHROME_USE_SEND_REF_COUNT_0') as raised:
                asyncio.run(driver.submit_prompt(
                    prompt='hello', turn_id='turn-context', actor_kind='WORKER', conversation_url=None
                ))
            self.assertEqual('@e11', raised.exception.diagnostics['editor_ref'])
            self.assertEqual(5, raised.exception.diagnostics['prompt_length'])
            self.assertEqual(64, len(raised.exception.diagnostics['prompt_sha256']))

    def test_submit_repairs_native_fill_with_key_event_type_before_click(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            conversation = 'https://chatgpt.com/c/live-key-event-repair'
            cli.responses = [
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/'}},
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                # The native fill did not activate ChatGPT's controlled-input
                # state, so the first post-fill snapshot has no Send control.
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {'data': {'refs': {
                    'e11': {'name': 'Message ChatGPT', 'role': 'textbox'},
                    'e20': {'name': 'Send', 'role': 'button'},
                }}},
                {'success': True},
                {'data': {'value': conversation}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'submitted'}},
            ]
            driver = self._driver(td, cli)
            result = asyncio.run(driver.submit_prompt(
                prompt='hello', turn_id='turn-key-event-repair', actor_kind='WORKER', conversation_url=None
            ))
            self.assertIn(conversation, result)
            type_calls = [args for _, args, _ in cli.calls if args and args[0] == 'type']
            self.assertEqual([['type', '@e11', 'hello', '--key-events', '--clear']], type_calls)
            click_calls = [args for _, args, _ in cli.calls if args and args[0] == 'click']
            self.assertEqual([['click', '@e20']], click_calls)

    def test_new_worker_turn_uses_a_new_owned_tab(self):
        with tempfile.TemporaryDirectory() as td:
            cli = NewTabFakeCli()
            conversation = 'https://chatgpt.com/c/new-owned-tab'
            cli.responses = [
                {'data': {'value': 'https://chatgpt.com/'}},
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {'data': {'refs': {
                    'e11': {'name': 'Message ChatGPT', 'role': 'textbox'},
                    'e20': {'name': 'Send', 'role': 'button'},
                }}},
                {'success': True},
                {'data': {'value': conversation}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'submitted in new owned tab'}},
            ]
            driver = self._driver(td, cli)
            result = asyncio.run(driver.submit_prompt(
                prompt='hello', turn_id='turn-new-owned-tab', actor_kind='WORKER', conversation_url=None
            ))
            self.assertIn(conversation, result)
            self.assertEqual(['tab', 'new', 'https://chatgpt.com/'], cli.calls[0][1])
            self.assertFalse(any(args and args[0] == 'open' for _, args, _ in cli.calls))

    def test_submit_does_not_key_event_retry_when_stop_generating_is_visible(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            cli.responses = [
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/'}},
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {'data': {'refs': {
                    'e11': {'name': 'Message ChatGPT', 'role': 'textbox'},
                    'e21': {'name': 'Stop generating', 'role': 'button'},
                }}},
            ]
            driver = self._driver(td, cli)
            with self.assertRaisesRegex(ValueError, 'CHROME_USE_SEND_REF_COUNT_0'):
                asyncio.run(driver.submit_prompt(
                    prompt='hello', turn_id='turn-stop-visible', actor_kind='WORKER', conversation_url=None
                ))
            self.assertFalse(any(args and args[0] == 'type' for _, args, _ in cli.calls))

    def test_submit_uses_single_key_event_enter_fallback_when_send_stays_missing(self):
        with tempfile.TemporaryDirectory() as td:
            cli = FakeCli()
            conversation = 'https://chatgpt.com/c/live-key-event-enter'
            cli.responses = [
                {'success': True},
                {'data': {'value': 'https://chatgpt.com/'}},
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                # Native fill did not activate the controlled composer.
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                # The repair restored the editor but the Send control is still
                # absent, so the driver may use exactly one explicit Enter key.
                {'data': {'refs': {'e11': {'name': 'Message ChatGPT', 'role': 'textbox'}}}},
                {'success': True},
                {'data': {'value': conversation}},
                {'success': True, 'data': {'broughtToFront': True}},
                {'data': {'snapshot': 'submitted by enter'}},
            ]
            driver = self._driver(td, cli)
            result = asyncio.run(driver.submit_prompt(
                prompt='hello', turn_id='turn-key-event-enter', actor_kind='WORKER', conversation_url=None
            ))
            self.assertIn(conversation, result)
            type_calls = [args for _, args, _ in cli.calls if args and args[0] == 'type']
            self.assertEqual(
                [['type', '@e11', 'hello', '--key-events', '--clear']],
                type_calls,
            )
            press_calls = [args for _, args, _ in cli.calls if args and args[0] == 'press']
            self.assertEqual([['press', 'Enter', '--selector', '@e11']], press_calls)
            self.assertFalse(any(args and args[0] == 'click' for _, args, _ in cli.calls))

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
