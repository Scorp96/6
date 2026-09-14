import ctypes
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path

from broker_core import RequestLedger, canonical_request_bytes, request_hash, validate_request_identity, validate_request_freshness
from broker_ops import dispatch_operation, validate_operation

PIPE_NAME = r'\\.\pipe\ScorpPrivilegedBrokerV1'
RESPONSE_PROTOCOL = 'scorp.broker/response-v1'
INSTALL_ROOT = Path(r'C:\ScorpAgent\privileged-broker')
STATE_ROOT = Path(r'C:\ProgramData\ScorpAgent\privileged-broker')
MAX_MESSAGE_BYTES = 1024 * 1024


def decode_request_message(raw: bytes) -> dict:
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError('REQUEST_MESSAGE_INVALID')
    try:
        value = json.loads(bytes(raw).decode('utf-8'))
    except Exception as exc:
        raise ValueError('REQUEST_MESSAGE_INVALID') from exc
    if not isinstance(value, dict):
        raise ValueError('REQUEST_MESSAGE_INVALID')
    return value

def encode_response_message(response: dict) -> bytes:
    return json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _result_hash(result: dict) -> str:
    raw = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _safe_request_hash(request) -> str | None:
    try:
        return request_hash(request) if isinstance(request, dict) else None
    except Exception:
        return None


def _error_name(exc: Exception) -> str:
    text = str(exc).strip()
    return text if text and len(text) <= 160 else type(exc).__name__


class BrokerHandler:
    def __init__(self, secret: bytes, ledger_path, audit_path, backend, service_identity='BROKER'):
        self.secret = secret
        self.ledger = RequestLedger(ledger_path)
        self.audit_path = Path(audit_path)
        self.backend = backend
        self.service_identity = service_identity
    def _audit(self, request, status: str, result: dict | None = None, error: str | None = None):
        entry = {
            'at': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
            'request_id': request.get('request_id') if isinstance(request, dict) else None,
            'operation': request.get('operation') if isinstance(request, dict) else None,
            'request_sha256': _safe_request_hash(request),
            'status': status,
            'service_identity': self.service_identity,
        }
        if result is not None:
            entry['result_sha256'] = _result_hash(result)
        if error:
            entry['error'] = error
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
        with self.audit_path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _response(self, request_id, status: str, **extra):
        value = {'protocol_version': RESPONSE_PROTOCOL, 'request_id': request_id, 'status': status}
        value.update(extra)
        return value

    def handle(self, request: dict, now: dt.datetime | None = None) -> dict:
        request_id = request.get('request_id') if isinstance(request, dict) else None
        try:
            validated = validate_request_identity(request, self.secret)
            validate_operation(validated['operation'], validated['params'])
            cached = self.ledger.lookup(validated)
            if cached is not None:
                self._audit(validated, 'REPLAY', result=cached)
                return self._response(request_id, 'OK', result=cached, replayed=True)
            validated = validate_request_freshness(validated, now=now)
            self.ledger.mark_inflight(validated)
        except Exception as exc:
            error = _error_name(exc)
            self._audit(request, 'REJECTED', error=error)
            return self._response(request_id, 'ERROR', error=error)

        try:
            result = dispatch_operation(validated['operation'], validated['params'], self.backend)
            stored = self.ledger.complete(validated, result)
            self._audit(validated, 'EXECUTED', result=stored)
            return self._response(request_id, 'OK', result=stored, replayed=False)
        except Exception as exc:
            error = _error_name(exc)
            self._audit(validated, 'ERROR', error=error)
            return self._response(request_id, 'ERROR', error=error)

class WindowsBackend:
    def identity_get(self):
        import win32api
        import win32con
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            name, domain, _ = win32security.LookupAccountSid(None, sid)
            sid_text = win32security.ConvertSidToStringSid(sid)
        finally:
            token.Close()
        session_id = ctypes.c_uint32()
        ok = ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id))
        if not ok:
            raise OSError('SESSION_ID_LOOKUP_FAILED')
        identity = f'{domain}\\{name}' if domain else name
        return {'identity': identity, 'sid': sid_text, 'pid': os.getpid(),
                'session_id': int(session_id.value), 'is_local_system': sid_text == 'S-1-5-18'}

    def _service_state(self, name):
        import win32service
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            svc = win32service.OpenService(scm, name, win32service.SERVICE_QUERY_STATUS)
            try:
                status = win32service.QueryServiceStatus(svc)
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
        return int(status[1])
    def service_get(self, name):
        import win32service
        names = {
            win32service.SERVICE_STOPPED: 'Stopped',
            win32service.SERVICE_START_PENDING: 'StartPending',
            win32service.SERVICE_STOP_PENDING: 'StopPending',
            win32service.SERVICE_RUNNING: 'Running',
            win32service.SERVICE_CONTINUE_PENDING: 'ContinuePending',
            win32service.SERVICE_PAUSE_PENDING: 'PausePending',
            win32service.SERVICE_PAUSED: 'Paused',
        }
        state = self._service_state(name)
        return {'name': name, 'state_code': state, 'status': names.get(state, 'Unknown')}

    def _wait_service_state(self, name, desired, timeout_seconds=30):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._service_state(name) == desired:
                return
            time.sleep(0.25)
        raise TimeoutError('SERVICE_STATE_TIMEOUT')

    def service_restart(self, name):
        import pywintypes
        import win32service
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            access = win32service.SERVICE_STOP | win32service.SERVICE_START | win32service.SERVICE_QUERY_STATUS
            svc = win32service.OpenService(scm, name, access)
            try:
                state = int(win32service.QueryServiceStatus(svc)[1])
                if state != win32service.SERVICE_STOPPED:
                    try:
                        win32service.ControlService(svc, win32service.SERVICE_CONTROL_STOP)
                    except pywintypes.error as exc:
                        if exc.winerror != 1062:
                            raise
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
        self._wait_service_state(name, win32service.SERVICE_STOPPED)
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            svc = win32service.OpenService(scm, name, win32service.SERVICE_START | win32service.SERVICE_QUERY_STATUS)
            try:
                win32service.StartService(svc, None)
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
        self._wait_service_state(name, win32service.SERVICE_RUNNING)
        return self.service_get(name)

    def task_get(self, name):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            scheduler = win32com.client.Dispatch('Schedule.Service')
            scheduler.Connect()
            task = scheduler.GetFolder('\\').GetTask(name)
            states = {0: 'Unknown', 1: 'Disabled', 2: 'Queued', 3: 'Ready', 4: 'Running'}
            state = int(task.State)
            return {'name': name, 'state_code': state, 'state': states.get(state, 'Unknown'),
                    'enabled': bool(task.Enabled)}
        finally:
            pythoncom.CoUninitialize()

    def task_run(self, name):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            scheduler = win32com.client.Dispatch('Schedule.Service')
            scheduler.Connect()
            running = scheduler.GetFolder('\\').GetTask(name).Run('')
            return {'name': name, 'started': True, 'instance_guid': str(running.InstanceGuid)}
        finally:
            pythoncom.CoUninitialize()
    def file_write(self, path, text):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + f'.tmp-{os.getpid()}')
        raw = text.encode('utf-8')
        with temp.open('wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return {'path': str(target), 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest()}

    def registry_set(self, path, name, value, value_type):
        import winreg
        prefix = 'HKLM\\'
        if not path.upper().startswith(prefix):
            raise ValueError('REGISTRY_PATH_NOT_ALLOWED')
        subkey = path[len(prefix):]
        kind = winreg.REG_SZ if value_type == 'string' else winreg.REG_DWORD
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, kind, value)
        return {'path': path, 'name': name, 'value_type': value_type}


def load_secret(path=STATE_ROOT / 'secret.key') -> bytes:
    raw = Path(path).read_bytes()
    if len(raw) != 32:
        raise ValueError('SECRET_INVALID')
    return raw

def load_config(path=STATE_ROOT / 'config.json') -> dict:
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or not value.get('allowed_user_sid'):
        raise ValueError('CONFIG_INVALID')
    return value


def pipe_security_attributes(allowed_user_sid: str):
    import pywintypes
    import win32security
    sid = str(allowed_user_sid)
    if not sid.startswith('S-1-'):
        raise ValueError('CONFIG_INVALID')
    sddl = f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{sid})'
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1)
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = descriptor
    return attrs


def build_production_handler():
    backend = WindowsBackend()
    identity = backend.identity_get()['identity']
    return BrokerHandler(load_secret(), STATE_ROOT / 'ledger.json',
                         STATE_ROOT / 'audit.jsonl', backend, identity)

def handle_raw_message(handler: BrokerHandler, raw: bytes) -> bytes:
    try:
        request = decode_request_message(raw)
    except Exception as exc:
        error = _error_name(exc)
        handler._audit({}, 'REJECTED', error=error)
        return encode_response_message({
            'protocol_version': RESPONSE_PROTOCOL,
            'request_id': None,
            'status': 'ERROR',
            'error': error,
        })
    return encode_response_message(handler.handle(request))


def create_named_pipe(allowed_user_sid: str, pipe_name: str = PIPE_NAME):
    import win32pipe
    mode = (win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE |
            win32pipe.PIPE_WAIT | win32pipe.PIPE_REJECT_REMOTE_CLIENTS)
    return win32pipe.CreateNamedPipe(
        pipe_name, win32pipe.PIPE_ACCESS_DUPLEX, mode, 1,
        MAX_MESSAGE_BYTES, MAX_MESSAGE_BYTES, 0,
        pipe_security_attributes(allowed_user_sid))

def _stop_requested(stop_event) -> bool:
    import win32event
    return win32event.WaitForSingleObject(stop_event, 0) == win32event.WAIT_OBJECT_0


def serve_forever(stop_event, handler: BrokerHandler, allowed_user_sid: str,
                  pipe_name: str = PIPE_NAME):
    import pywintypes
    import win32file
    import win32pipe
    while not _stop_requested(stop_event):
        pipe = create_named_pipe(allowed_user_sid, pipe_name)
        try:
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:  # ERROR_PIPE_CONNECTED
                    raise
            if _stop_requested(stop_event):
                break
            try:
                _, raw = win32file.ReadFile(pipe, MAX_MESSAGE_BYTES)
                response = handle_raw_message(handler, bytes(raw))
                win32file.WriteFile(pipe, response)
                win32file.FlushFileBuffers(pipe)
            except pywintypes.error:
                if not _stop_requested(stop_event):
                    raise
        finally:
            try:
                win32pipe.DisconnectNamedPipe(pipe)
            except Exception:
                pass
            win32file.CloseHandle(pipe)

def wake_pipe(pipe_name: str = PIPE_NAME):
    import pywintypes
    import win32con
    import win32file
    import win32pipe
    try:
        win32pipe.WaitNamedPipe(pipe_name, 1000)
        handle = win32file.CreateFile(
            pipe_name, win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            0, None, win32con.OPEN_EXISTING, 0, None)
        win32file.CloseHandle(handle)
    except pywintypes.error:
        pass


import win32event
import win32service
import win32serviceutil


class ScorpPrivilegedBrokerService(win32serviceutil.ServiceFramework):
    _svc_name_ = 'ScorpPrivilegedBroker'
    _svc_display_name_ = 'Scorp Privileged Broker'
    _svc_description_ = 'LocalSystem allowlisted privilege broker for Scorp Agent.'

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        wake_pipe()

    def SvcDoRun(self):
        import servicemanager
        try:
            config = load_config()
            handler = build_production_handler()
            servicemanager.LogInfoMsg('ScorpPrivilegedBroker starting')
            serve_forever(self.stop_event, handler, config['allowed_user_sid'])
            servicemanager.LogInfoMsg('ScorpPrivilegedBroker stopped')
        except Exception as exc:
            servicemanager.LogErrorMsg(f'ScorpPrivilegedBroker fatal: {type(exc).__name__}: {exc}')
            raise


def run_service_host():
    import servicemanager
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(ScorpPrivilegedBrokerService)
    servicemanager.StartServiceCtrlDispatcher()


if __name__ == '__main__':
    import sys
    if '--service-host' not in sys.argv:
        raise SystemExit('Use --service-host; installation is managed by install-broker.ps1')
    run_service_host()
