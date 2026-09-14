import datetime as dt
import json
import os
import uuid
from pathlib import Path

from broker_core import PROTOCOL, sign_request, validate_request_identity

PIPE_NAME = r'\\.\pipe\ScorpPrivilegedBrokerV1'
RESPONSE_PROTOCOL = 'scorp.broker/response-v1'
SECRET_PATH = Path(r'C:\ProgramData\ScorpAgent\privileged-broker\secret.key')


def _iso_z(value: dt.datetime) -> str:
    value = value.astimezone(dt.timezone.utc)
    return value.isoformat().replace('+00:00', 'Z')


def build_signed_request(operation: str, params: dict, secret: bytes,
                         request_id: str | None = None,
                         now: dt.datetime | None = None,
                         ttl_seconds: int = 60) -> dict:
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    request = {
        'protocol_version': PROTOCOL,
        'request_id': request_id or str(uuid.uuid4()),
        'operation': operation,
        'params': params,
        'issued_at': _iso_z(now),
        'expires_at': _iso_z(now + dt.timedelta(seconds=ttl_seconds)),
    }
    return sign_request(request, secret)


def _validate_response_identity(response: dict, request_id: str) -> None:
    if not isinstance(response, dict) or response.get('protocol_version') != RESPONSE_PROTOCOL:
        raise ValueError('RESPONSE_PROTOCOL_INVALID')
    if response.get('request_id') != request_id:
        raise ValueError('RESPONSE_REQUEST_MISMATCH')


def validate_response(response: dict, request_id: str):
    _validate_response_identity(response, request_id)
    status = response.get('status')
    if status == 'ERROR':
        raise RuntimeError(str(response.get('error') or 'BROKER_ERROR'))
    if status != 'OK' or not isinstance(response.get('result'), dict):
        raise ValueError('RESPONSE_SCHEMA_INVALID')
    return response['result']


def load_secret(path) -> bytes:
    raw = Path(path).read_bytes()
    if len(raw) != 32:
        raise ValueError('SECRET_INVALID')
    return raw


def _atomic_write_request(path: Path, request: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.tmp-{os.getpid()}-{uuid.uuid4().hex}')
    raw = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    try:
        with temp.open('wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def get_or_create_request(path, operation: str, params: dict, secret: bytes,
                          request_id: str, now: dt.datetime | None = None,
                          ttl_seconds: int = 60) -> dict:
    target = Path(path)
    if not request_id:
        raise ValueError('REQUEST_ID_INVALID')
    if not isinstance(params, dict):
        raise ValueError('REQUEST_PARAMS_INVALID')

    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding='utf-8-sig'))
        except Exception as exc:
            raise ValueError('REQUEST_ARTIFACT_INVALID') from exc
        try:
            validate_request_identity(existing, secret)
        except Exception as exc:
            raise ValueError('REQUEST_ARTIFACT_INVALID') from exc
        if (existing.get('request_id') != request_id or
                existing.get('operation') != operation or
                existing.get('params') != params):
            raise ValueError('REQUEST_ARTIFACT_CONFLICT')
        return existing

    request = build_signed_request(
        operation, params, secret, request_id=request_id,
        now=now, ttl_seconds=ttl_seconds)
    _atomic_write_request(target, request)
    return request


def _refresh_request_after_proven_expiry(path, operation: str, params: dict,
                                         secret: bytes, request_id: str,
                                         now: dt.datetime | None = None) -> dict:
    target = Path(path)
    request = build_signed_request(
        operation, params, secret, request_id=request_id, now=now)
    _atomic_write_request(target, request)
    return request


def pipe_round_trip(request: dict, pipe_name: str = PIPE_NAME, timeout_ms: int = 10000) -> dict:
    import win32con
    import win32file
    import win32pipe
    win32pipe.WaitNamedPipe(pipe_name, timeout_ms)
    handle = win32file.CreateFile(
        pipe_name, win32con.GENERIC_READ | win32con.GENERIC_WRITE,
        0, None, win32con.OPEN_EXISTING, 0, None)
    try:
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        payload = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        win32file.WriteFile(handle, payload)
        _, data = win32file.ReadFile(handle, 1024 * 1024)
        return json.loads(bytes(data).decode('utf-8'))
    finally:
        win32file.CloseHandle(handle)


def execute_persisted_request(path, operation: str, params: dict, secret: bytes,
                              request_id: str, now: dt.datetime | None = None,
                              refresh_now: dt.datetime | None = None,
                              transport=None) -> dict:
    request = get_or_create_request(
        path, operation, params, secret, request_id, now=now)
    send = transport or pipe_round_trip
    response = send(request)
    _validate_response_identity(response, request_id)

    if response.get('status') == 'ERROR' and response.get('error') == 'REQUEST_EXPIRED':
        request = _refresh_request_after_proven_expiry(
            path, operation, params, secret, request_id,
            now=refresh_now or dt.datetime.now(dt.timezone.utc))
        response = send(request)

    result = validate_response(response, request_id)
    return {
        'request_id': request_id,
        'result': result,
        'replayed': bool(response.get('replayed')),
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('legacy_operation', nargs='?')
    parser.add_argument('--operation')
    parser.add_argument('--params-json', default='{}')
    parser.add_argument('--request-id')
    parser.add_argument('--request-file')
    parser.add_argument('--secret', default=str(SECRET_PATH))
    parser.add_argument('--pipe', default=PIPE_NAME)
    parser.add_argument('--timeout-ms', type=int, default=10000)
    args = parser.parse_args(argv)

    operation = args.operation or args.legacy_operation
    if args.operation and args.legacy_operation and args.operation != args.legacy_operation:
        parser.error('operation specified twice with different values')
    if not operation:
        parser.error('operation is required')

    params = json.loads(args.params_json)
    if not isinstance(params, dict):
        raise SystemExit('params must be a JSON object')

    if args.request_file:
        if not args.request_id:
            parser.error('--request-id is required with --request-file')
        if args.secret != str(SECRET_PATH) or args.pipe != PIPE_NAME or args.timeout_ms != 10000:
            parser.error('persisted request mode uses fixed broker transport and secret paths')
        value = execute_persisted_request(
            args.request_file, operation, params, load_secret(SECRET_PATH), args.request_id)
        print(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
        return 0

    request = build_signed_request(
        operation, params, load_secret(args.secret), request_id=args.request_id)
    response = pipe_round_trip(request, args.pipe, args.timeout_ms)
    result = validate_response(response, request['request_id'])
    print(json.dumps({'request_id': request['request_id'], 'result': result,
                      'replayed': bool(response.get('replayed'))},
                     ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
