import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from broker_core import (
    RequestLedger, canonical_request_bytes, request_hash,
    sign_request, validate_request,
)

SECRET = b'x' * 32
NOW = dt.datetime(2026, 9, 11, 2, 30, tzinfo=dt.timezone.utc)


def base_request(request_id='req-1'):
    return {
        'protocol_version': 'scorp.broker/v1',
        'request_id': request_id,
        'operation': 'identity.get',
        'params': {},
        'issued_at': '2026-09-11T02:29:00Z',
        'expires_at': '2026-09-11T02:31:00Z',
    }


class BrokerCoreTests(unittest.TestCase):
    def test_canonical_bytes_ignore_auth_and_sort_keys(self):
        req = base_request()
        req['params'] = {'z': 1, 'a': 2}
        req['auth'] = 'ignored'
        raw = canonical_request_bytes(req)
        self.assertNotIn(b'auth', raw)
        self.assertEqual(raw, json.dumps({k: v for k, v in req.items() if k != 'auth'}, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())

    def test_sign_and_validate(self):
        signed = sign_request(base_request(), SECRET)
        self.assertTrue(signed['auth'])
        validated = validate_request(signed, SECRET, NOW)
        self.assertEqual(validated['request_id'], 'req-1')

    def test_invalid_auth_and_expiry_fail_closed(self):
        signed = sign_request(base_request(), SECRET)
        signed['auth'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'AUTH_INVALID'):
            validate_request(signed, SECRET, NOW)
        expired = sign_request(base_request(), SECRET)
        with self.assertRaisesRegex(ValueError, 'REQUEST_EXPIRED'):
            validate_request(expired, SECRET, NOW + dt.timedelta(minutes=5))

    def test_replay_returns_cached_result_and_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = RequestLedger(Path(td) / 'ledger.json')
            req = sign_request(base_request('req-replay'), SECRET)
            self.assertIsNone(ledger.lookup(req))
            ledger.mark_inflight(req)
            result = {'ok': True, 'value': 7}
            ledger.complete(req, result)
            self.assertEqual(ledger.lookup(req), result)
            conflict = sign_request({**base_request('req-replay'), 'operation': 'service.get', 'params': {'name': 'ScorpX'}}, SECRET)
            with self.assertRaisesRegex(ValueError, 'REQUEST_ID_CONFLICT'):
                ledger.lookup(conflict)

    def test_inflight_replay_and_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'ledger.json'
            req = sign_request(base_request('req-live'), SECRET)
            ledger = RequestLedger(path)
            ledger.mark_inflight(req)
            with self.assertRaisesRegex(ValueError, 'REQUEST_INFLIGHT'):
                RequestLedger(path).lookup(req)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['req-live']['state'], 'INFLIGHT')
            self.assertEqual(data['req-live']['request_sha256'], request_hash(req))

    def test_inflight_reconciliation_requires_explicit_disposition_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'ledger.json'
            req = sign_request(base_request('req-reconcile'), SECRET)
            ledger = RequestLedger(path)
            ledger.mark_inflight(req)
            with self.assertRaisesRegex(ValueError, 'RECONCILIATION_DISPOSITION_INVALID'):
                ledger.reconcile_inflight(req, disposition='RETRY')
            with self.assertRaisesRegex(ValueError, 'RECONCILIATION_EVIDENCE_REQUIRED'):
                ledger.reconcile_inflight(req, disposition='BLOCKED_AMBIGUOUS')
            ledger.reconcile_inflight(
                req,
                disposition='BLOCKED_AMBIGUOUS',
                evidence_ref='crash-window-001',
            )
            with self.assertRaisesRegex(ValueError, 'REQUEST_AMBIGUOUS'):
                ledger.lookup(req)
            entry = json.loads(path.read_text(encoding='utf-8'))['req-reconcile']
            self.assertEqual(entry['state'], 'BLOCKED_AMBIGUOUS')
            self.assertEqual(entry['evidence_ref'], 'crash-window-001')

    def test_inflight_reconciliation_done_allows_verified_replay_without_execution(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'ledger.json'
            req = sign_request(base_request('req-reconcile-done'), SECRET)
            ledger = RequestLedger(path)
            ledger.mark_inflight(req)
            result = {'ok': True, 'value': 9}
            ledger.reconcile_inflight(
                req,
                disposition='DONE_CONFIRMED',
                result=result,
                evidence_ref='external-receipt-9',
            )
            self.assertEqual(ledger.lookup(req), result)
            entry = json.loads(path.read_text(encoding='utf-8'))['req-reconcile-done']
            self.assertEqual(entry['state'], 'DONE')
            self.assertEqual(entry['reconciliation_evidence_ref'], 'external-receipt-9')

    def test_mark_inflight_survives_brief_windows_destination_share_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'ledger.json'
            path.write_text('{}\n', encoding='utf-8')
            ps_path = str(path).replace("'", "''")
            command = (
                "$fs=[IO.File]::Open('" + ps_path + "',[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);"
                "[Console]::Out.WriteLine('READY');[Console]::Out.Flush();"
                "Start-Sleep -Milliseconds 200;$fs.Dispose()"
            )
            holder = subprocess.Popen(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'READY')
                req = sign_request(base_request('req-share-conflict'), SECRET)
                RequestLedger(path).mark_inflight(req)
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['req-share-conflict']['state'], 'INFLIGHT')
            finally:
                holder.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
