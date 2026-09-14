import copy
import datetime as dt
import hashlib
import hmac
import json
import time
from pathlib import Path

PROTOCOL = 'scorp.broker/v1'


def canonical_request_bytes(request: dict) -> bytes:
    body = {k: v for k, v in request.items() if k != 'auth'}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def request_hash(request: dict) -> str:
    return hashlib.sha256(canonical_request_bytes(request)).hexdigest()


def sign_request(request: dict, secret: bytes) -> dict:
    signed = copy.deepcopy(request)
    signed.pop('auth', None)
    signed['auth'] = hmac.new(secret, canonical_request_bytes(signed), hashlib.sha256).hexdigest()
    return signed


def _parse_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception as exc:
        raise ValueError('REQUEST_TIME_INVALID') from exc
    if parsed.tzinfo is None:
        raise ValueError('REQUEST_TIME_INVALID')
    return parsed.astimezone(dt.timezone.utc)


def validate_request_identity(request: dict, secret: bytes) -> dict:
    required = ('protocol_version', 'request_id', 'operation', 'params', 'issued_at', 'expires_at', 'auth')
    if not isinstance(request, dict) or any(k not in request for k in required):
        raise ValueError('REQUEST_SCHEMA_INVALID')
    if request.get('protocol_version') != PROTOCOL:
        raise ValueError('PROTOCOL_INVALID')
    if not isinstance(request.get('request_id'), str) or not request['request_id']:
        raise ValueError('REQUEST_ID_INVALID')
    if not isinstance(request.get('operation'), str) or not isinstance(request.get('params'), dict):
        raise ValueError('REQUEST_SCHEMA_INVALID')
    expected = hmac.new(secret, canonical_request_bytes(request), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(request.get('auth')), expected):
        raise ValueError('AUTH_INVALID')

    issued_at = _parse_time(request['issued_at'])
    expires_at = _parse_time(request['expires_at'])
    if expires_at <= issued_at:
        raise ValueError('REQUEST_TIME_INVALID')
    return copy.deepcopy(request)


def validate_request_freshness(request: dict, now: dt.datetime | None = None) -> dict:
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    issued_at = _parse_time(request['issued_at'])
    expires_at = _parse_time(request['expires_at'])
    if expires_at <= issued_at:
        raise ValueError('REQUEST_TIME_INVALID')
    if expires_at <= now:
        raise ValueError('REQUEST_EXPIRED')
    if issued_at > now + dt.timedelta(minutes=5):
        raise ValueError('REQUEST_ISSUED_IN_FUTURE')
    return copy.deepcopy(request)


def validate_request(request: dict, secret: bytes, now: dt.datetime | None = None) -> dict:
    validated = validate_request_identity(request, secret)
    return validate_request_freshness(validated, now=now)


def _atomic_write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
    tmp.write_text(text, encoding='utf-8', newline='\n')
    deadline = time.monotonic() + 1.0
    while True:
        try:
            tmp.replace(path)
            return
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32) or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


class RequestLedger:
    def __init__(self, path):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        value = json.loads(self.path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('LEDGER_INVALID')
        return value

    def lookup(self, request: dict):
        entry = self._load().get(request['request_id'])
        if entry is None:
            return None
        if entry.get('request_sha256') != request_hash(request):
            raise ValueError('REQUEST_ID_CONFLICT')
        if entry.get('state') == 'INFLIGHT':
            raise ValueError('REQUEST_INFLIGHT')
        if entry.get('state') == 'BLOCKED_AMBIGUOUS':
            raise ValueError('REQUEST_AMBIGUOUS')
        if entry.get('state') != 'DONE' or 'result' not in entry:
            raise ValueError('LEDGER_ENTRY_INVALID')
        return copy.deepcopy(entry['result'])

    def reconcile_inflight(
        self,
        request: dict,
        *,
        disposition: str,
        result: dict | None = None,
        evidence_ref: str | None = None,
    ):
        """Record an explicit post-crash disposition for an INFLIGHT request.

        The broker cannot infer whether a privileged side effect happened when
        the process died between dispatch and ledger completion.  Recovery must
        therefore be driven by an external receipt or an explicit ambiguity
        record; this method never turns an uncertain request into a retry.
        """
        if disposition not in {'DONE_CONFIRMED', 'BLOCKED_AMBIGUOUS'}:
            raise ValueError('RECONCILIATION_DISPOSITION_INVALID')
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError('RECONCILIATION_EVIDENCE_REQUIRED')
        value = self._load()
        rid = request.get('request_id')
        entry = value.get(rid)
        if not entry or entry.get('request_sha256') != request_hash(request):
            raise ValueError('REQUEST_LEDGER_MISMATCH')
        if entry.get('state') != 'INFLIGHT':
            raise ValueError('REQUEST_NOT_INFLIGHT')
        if disposition == 'DONE_CONFIRMED':
            if not isinstance(result, dict):
                raise ValueError('RECONCILIATION_RESULT_REQUIRED')
            stored = copy.deepcopy(result)
            entry['state'] = 'DONE'
            entry['result'] = stored
            entry['result_sha256'] = hashlib.sha256(
                json.dumps(stored, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            ).hexdigest()
            entry['reconciliation_evidence_ref'] = evidence_ref.strip()
        else:
            entry['state'] = 'BLOCKED_AMBIGUOUS'
            entry['evidence_ref'] = evidence_ref.strip()
        entry['reconciled_at'] = dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')
        _atomic_write_json(self.path, value)
        return copy.deepcopy(entry)

    def mark_inflight(self, request: dict):
        value = self._load()
        rid = request['request_id']
        if rid in value:
            self.lookup(request)
            raise ValueError('REQUEST_ALREADY_DONE')
        value[rid] = {
            'state': 'INFLIGHT',
            'request_sha256': request_hash(request),
        }
        _atomic_write_json(self.path, value)

    def complete(self, request: dict, result: dict):
        value = self._load()
        rid = request['request_id']
        entry = value.get(rid)
        if not entry or entry.get('request_sha256') != request_hash(request):
            raise ValueError('REQUEST_LEDGER_MISMATCH')
        stored = copy.deepcopy(result)
        entry['state'] = 'DONE'
        entry['result'] = stored
        entry['result_sha256'] = hashlib.sha256(
            json.dumps(stored, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
        _atomic_write_json(self.path, value)
        return copy.deepcopy(stored)
