import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from broker_client import execute_persisted_request, get_or_create_request, main


NOW = dt.datetime(2026, 9, 11, 4, 30, tzinfo=dt.timezone.utc)
SECRET = b'k' * 32
REQUEST_ID = 'v4.' + ('a' * 64)


class BrokerClientPersistedRequestTests(unittest.TestCase):
    def test_get_or_create_persists_and_reuses_exact_signed_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'request.json'
            first = get_or_create_request(
                path, 'identity.get', {}, SECRET, REQUEST_ID, now=NOW)
            raw_first = path.read_bytes()
            second = get_or_create_request(
                path, 'identity.get', {}, SECRET, REQUEST_ID,
                now=NOW + dt.timedelta(seconds=30))
            self.assertEqual(first, second)
            self.assertEqual(path.read_bytes(), raw_first)
            self.assertEqual(first['request_id'], REQUEST_ID)
            self.assertEqual(first['operation'], 'identity.get')

    def test_get_or_create_rejects_conflicting_existing_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'request.json'
            get_or_create_request(path, 'identity.get', {}, SECRET, REQUEST_ID, now=NOW)
            with self.assertRaisesRegex(ValueError, 'REQUEST_ARTIFACT_CONFLICT'):
                get_or_create_request(
                    path, 'service.get', {'name': 'ScorpPrivilegedBroker'},
                    SECRET, REQUEST_ID, now=NOW + dt.timedelta(seconds=1))

    def test_execute_persists_request_before_transport_and_returns_bound_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'request.json'
            observed = {}

            def transport(request):
                observed['exists'] = path.is_file()
                observed['stored'] = json.loads(path.read_text(encoding='utf-8'))
                observed['sent'] = request
                return {
                    'protocol_version': 'scorp.broker/response-v1',
                    'request_id': REQUEST_ID,
                    'status': 'OK',
                    'result': {'identity': r'NT AUTHORITY\SYSTEM'},
                    'replayed': False,
                }

            value = execute_persisted_request(
                path, 'identity.get', {}, SECRET, REQUEST_ID,
                now=NOW, transport=transport)
            self.assertTrue(observed['exists'])
            self.assertEqual(observed['stored'], observed['sent'])
            self.assertEqual(value['request_id'], REQUEST_ID)
            self.assertFalse(value['replayed'])
            self.assertEqual(value['result']['identity'], r'NT AUTHORITY\SYSTEM')

    def test_request_expired_refreshes_same_id_once_after_broker_proof(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'request.json'
            sent = []

            def transport(request):
                sent.append(json.loads(json.dumps(request)))
                if len(sent) == 1:
                    return {
                        'protocol_version': 'scorp.broker/response-v1',
                        'request_id': REQUEST_ID,
                        'status': 'ERROR',
                        'error': 'REQUEST_EXPIRED',
                    }
                return {
                    'protocol_version': 'scorp.broker/response-v1',
                    'request_id': REQUEST_ID,
                    'status': 'OK',
                    'result': {'identity': r'NT AUTHORITY\SYSTEM'},
                    'replayed': False,
                }

            value = execute_persisted_request(
                path, 'identity.get', {}, SECRET, REQUEST_ID,
                now=NOW, refresh_now=NOW + dt.timedelta(minutes=2),
                transport=transport)
            self.assertEqual(len(sent), 2)
            self.assertEqual(sent[0]['request_id'], REQUEST_ID)
            self.assertEqual(sent[1]['request_id'], REQUEST_ID)
            self.assertNotEqual(sent[0]['issued_at'], sent[1]['issued_at'])
            self.assertNotEqual(sent[0]['auth'], sent[1]['auth'])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), sent[1])
            self.assertEqual(value['result']['identity'], r'NT AUTHORITY\SYSTEM')

    def test_request_expired_refresh_is_bounded_to_one_retry(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'request.json'
            calls = []

            def transport(request):
                calls.append(request)
                return {
                    'protocol_version': 'scorp.broker/response-v1',
                    'request_id': REQUEST_ID,
                    'status': 'ERROR',
                    'error': 'REQUEST_EXPIRED',
                }

            with self.assertRaisesRegex(RuntimeError, 'REQUEST_EXPIRED'):
                execute_persisted_request(
                    path, 'identity.get', {}, SECRET, REQUEST_ID,
                    now=NOW, refresh_now=NOW + dt.timedelta(minutes=2),
                    transport=transport)
            self.assertEqual(len(calls), 2)

    def test_inflight_or_conflict_never_refreshes_artifact(self):
        for error in ('REQUEST_INFLIGHT', 'REQUEST_ID_CONFLICT'):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as td:
                path = Path(td) / 'request.json'
                get_or_create_request(path, 'identity.get', {}, SECRET, REQUEST_ID, now=NOW)
                raw_before = path.read_bytes()
                calls = []

                def transport(request):
                    calls.append(request)
                    return {
                        'protocol_version': 'scorp.broker/response-v1',
                        'request_id': REQUEST_ID,
                        'status': 'ERROR',
                        'error': error,
                    }

                with self.assertRaisesRegex(RuntimeError, error):
                    execute_persisted_request(
                        path, 'identity.get', {}, SECRET, REQUEST_ID,
                        now=NOW, refresh_now=NOW + dt.timedelta(minutes=2),
                        transport=transport)
                self.assertEqual(len(calls), 1)
                self.assertEqual(path.read_bytes(), raw_before)

    def test_main_accepts_runner_request_file_contract(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'request.json'
            expected = {
                'request_id': REQUEST_ID,
                'result': {'identity': r'NT AUTHORITY\SYSTEM'},
                'replayed': False,
            }
            with mock.patch('broker_client.load_secret', return_value=SECRET), \
                 mock.patch('broker_client.execute_persisted_request', return_value=expected) as execute, \
                 mock.patch('builtins.print') as printed:
                code = main([
                    '--request-file', str(path),
                    '--request-id', REQUEST_ID,
                    '--operation', 'identity.get',
                    '--params-json', '{}',
                ])
            self.assertEqual(code, 0)
            execute.assert_called_once()
            args = execute.call_args.args
            self.assertEqual(Path(args[0]), path)
            self.assertEqual(args[1], 'identity.get')
            self.assertEqual(args[2], {})
            self.assertEqual(args[4], REQUEST_ID)
            printed.assert_called_once()


if __name__ == '__main__':
    unittest.main()
