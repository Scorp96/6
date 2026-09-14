import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from broker_client import build_signed_request, validate_response
from broker_service import BrokerHandler, decode_request_message, encode_response_message


NOW = dt.datetime(2026, 9, 11, 3, 0, tzinfo=dt.timezone.utc)
SECRET = b'k' * 32


class FakeBackend:
    def __init__(self):
        self.calls = []

    def identity_get(self):
        self.calls.append(('identity.get', {}))
        return {'identity': r'NT AUTHORITY\SYSTEM', 'is_local_system': True, 'pid': 42, 'session_id': 0}

    def service_get(self, name):
        self.calls.append(('service.get', {'name': name}))
        return {'name': name, 'status': 'Running'}
    def service_restart(self, name):
        self.calls.append(('service.restart', {'name': name}))
        return {'name': name, 'status': 'Running'}

    def task_get(self, name):
        self.calls.append(('task.get', {'name': name}))
        return {'name': name, 'state': 'Ready'}

    def task_run(self, name):
        self.calls.append(('task.run', {'name': name}))
        return {'name': name, 'started': True}

    def file_write(self, path, text):
        self.calls.append(('file.write', {'path': path, 'text': text}))
        return {'path': path, 'bytes': len(text.encode('utf-8'))}

    def registry_set(self, path, name, value, value_type):
        self.calls.append(('registry.set', {'path': path, 'name': name, 'value': value, 'value_type': value_type}))
        return {'path': path, 'name': name, 'value_type': value_type}


def make_handler(root: Path, backend=None):
    return BrokerHandler(SECRET, root / 'ledger.json', root / 'audit.jsonl', backend or FakeBackend())

class BrokerServiceTests(unittest.TestCase):
    def test_message_framing_roundtrip(self):
        request = {'a': 1, 'b': '中'}
        raw = json.dumps(request, ensure_ascii=False).encode('utf-8')
        self.assertEqual(decode_request_message(raw), request)
        response = {'protocol_version': 'scorp.broker/response-v1', 'request_id': 'r1', 'status': 'OK', 'result': {'x': 2}}
        self.assertEqual(json.loads(encode_response_message(response).decode('utf-8')), response)

    def test_authenticated_request_executes_and_replay_is_cached(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); backend = FakeBackend(); handler = make_handler(root, backend)
            request = build_signed_request('identity.get', {}, SECRET, request_id='req-1', now=NOW)
            first = handler.handle(request, now=NOW)
            second = handler.handle(request, now=NOW)
            self.assertEqual(first['status'], 'OK')
            self.assertFalse(first['replayed'])
            self.assertTrue(second['replayed'])
            self.assertEqual(first['result'], second['result'])
            self.assertEqual(backend.calls, [('identity.get', {})])

    def test_completed_request_replays_after_expiry_without_second_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); backend = FakeBackend(); handler = make_handler(root, backend)
            request = build_signed_request('identity.get', {}, SECRET, request_id='expired-done', now=NOW)
            first = handler.handle(request, now=NOW)
            replay = handler.handle(request, now=NOW + dt.timedelta(minutes=2))
            self.assertEqual(first['status'], 'OK')
            self.assertFalse(first['replayed'])
            self.assertEqual(replay['status'], 'OK')
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['result'], first['result'])
            self.assertEqual(backend.calls, [('identity.get', {})])

    def test_never_accepted_expired_request_is_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); backend = FakeBackend(); handler = make_handler(root, backend)
            request = build_signed_request('identity.get', {}, SECRET, request_id='expired-new', now=NOW)
            response = handler.handle(request, now=NOW + dt.timedelta(minutes=2))
            self.assertEqual(response['status'], 'ERROR')
            self.assertEqual(response['error'], 'REQUEST_EXPIRED')
            self.assertEqual(backend.calls, [])

    def test_invalid_auth_returns_bound_error_and_audits_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); handler = make_handler(root)
            request = build_signed_request('identity.get', {}, SECRET, request_id='req-bad', now=NOW)
            request['auth'] = '00' * 32
            response = handler.handle(request, now=NOW)
            self.assertEqual(response['request_id'], 'req-bad')
            self.assertEqual(response['status'], 'ERROR')
            self.assertEqual(response['error'], 'AUTH_INVALID')
            audit = [json.loads(x) for x in (root / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(audit[-1]['status'], 'REJECTED')
            self.assertNotIn('auth', audit[-1])

    def test_conflicting_request_id_is_rejected_without_second_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); backend = FakeBackend(); handler = make_handler(root, backend)
            one = build_signed_request('identity.get', {}, SECRET, request_id='same', now=NOW)
            handler.handle(one, now=NOW)
            two = build_signed_request('service.get', {'name': 'Spooler'}, SECRET, request_id='same', now=NOW)
            response = handler.handle(two, now=NOW)
            self.assertEqual(response['status'], 'ERROR')
            self.assertEqual(response['error'], 'REQUEST_ID_CONFLICT')
            self.assertEqual(backend.calls, [('identity.get', {})])
    def test_client_response_validation_binds_request_identity(self):
        ok = {'protocol_version': 'scorp.broker/response-v1', 'request_id': 'r1',
              'status': 'OK', 'result': {'identity': 'SYSTEM'}, 'replayed': False}
        self.assertEqual(validate_response(ok, 'r1')['identity'], 'SYSTEM')
        with self.assertRaisesRegex(ValueError, 'RESPONSE_REQUEST_MISMATCH'):
            validate_response(ok, 'other')
        with self.assertRaisesRegex(RuntimeError, 'AUTH_INVALID'):
            validate_response({'protocol_version': 'scorp.broker/response-v1',
                               'request_id': 'r1', 'status': 'ERROR',
                               'error': 'AUTH_INVALID'}, 'r1')

    def test_audit_records_execution_and_replay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); handler = make_handler(root)
            request = build_signed_request('identity.get', {}, SECRET, request_id='audit-1', now=NOW)
            handler.handle(request, now=NOW); handler.handle(request, now=NOW)
            audit = [json.loads(x) for x in (root / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([x['status'] for x in audit], ['EXECUTED', 'REPLAY'])
            self.assertTrue(all(x['request_id'] == 'audit-1' for x in audit))


if __name__ == '__main__':
    unittest.main()