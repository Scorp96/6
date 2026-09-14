import ntpath
import re

BROKER_SERVICE = 'ScorpPrivilegedBroker'
BROKER_DATA_ROOT = r'C:\ProgramData\ScorpAgent\broker-data'
REGISTRY_ROOT = r'HKLM\Software\ScorpAgent'
_ALLOWED = {
    'identity.get', 'service.get', 'service.restart',
    'task.get', 'task.run', 'file.write', 'registry.set',
}
_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+$')
_REGISTRY_NAME_RE = re.compile(r'^[^\\/\x00-\x1f]{1,255}$')


def _require_exact_keys(params: dict, keys: set[str]):
    if not isinstance(params, dict) or set(params) != keys:
        raise ValueError('PARAMS_INVALID')


def _service_name(value) -> str:
    name = str(value or '')
    if not _NAME_RE.fullmatch(name):
        raise ValueError('SERVICE_NAME_INVALID')
    return name

def _task_name(value) -> str:
    name = str(value or '')
    if not _NAME_RE.fullmatch(name) or not name.lower().startswith('scorp'):
        raise ValueError('TASK_NAME_NOT_ALLOWED')
    return name


def _scorp_service_name(value) -> str:
    name = _service_name(value)
    if not name.lower().startswith('scorp'):
        raise ValueError('SERVICE_NAME_NOT_ALLOWED')
    if name.lower() == BROKER_SERVICE.lower():
        raise ValueError('BROKER_SELF_RESTART_REJECTED')
    return name


def _file_path(value) -> str:
    raw = str(value or '')
    if not raw or ntpath.isabs(raw) or raw.startswith(('\\\\', '/')):
        raise ValueError('FILE_PATH_NOT_ALLOWED')
    candidate = ntpath.normpath(ntpath.join(BROKER_DATA_ROOT, raw))
    root = ntpath.normcase(ntpath.normpath(BROKER_DATA_ROOT))
    normalized = ntpath.normcase(candidate)
    if normalized == root or not normalized.startswith(root + '\\'):
        raise ValueError('FILE_PATH_NOT_ALLOWED')
    return candidate

def _registry_path(value) -> str:
    path = str(value or '').rstrip('\\')
    root = REGISTRY_ROOT.rstrip('\\')
    if path.lower() != root.lower() and not path.lower().startswith(root.lower() + '\\'):
        raise ValueError('REGISTRY_PATH_NOT_ALLOWED')
    return path


def validate_operation(operation: str, params: dict) -> dict:
    if operation not in _ALLOWED:
        raise ValueError('OPERATION_NOT_ALLOWED')
    if operation == 'identity.get':
        _require_exact_keys(params, set())
        return {}
    if operation == 'service.get':
        _require_exact_keys(params, {'name'})
        return {'name': _service_name(params['name'])}
    if operation == 'service.restart':
        _require_exact_keys(params, {'name'})
        return {'name': _scorp_service_name(params['name'])}
    if operation in ('task.get', 'task.run'):
        _require_exact_keys(params, {'name'})
        return {'name': _task_name(params['name'])}
    if operation == 'file.write':
        _require_exact_keys(params, {'path', 'text'})
        if not isinstance(params['text'], str):
            raise ValueError('PARAMS_INVALID')
        return {'path': _file_path(params['path']), 'text': params['text']}
    if operation == 'registry.set':
        _require_exact_keys(params, {'path', 'name', 'value', 'value_type'})
        value_type = str(params['value_type'] or '').lower()
        if value_type not in ('string', 'dword'):
            raise ValueError('REGISTRY_TYPE_NOT_ALLOWED')
        name = str(params['name'] or '')
        if not _REGISTRY_NAME_RE.fullmatch(name):
            raise ValueError('REGISTRY_NAME_INVALID')
        value = params['value']
        if value_type == 'string' and not isinstance(value, str):
            raise ValueError('REGISTRY_VALUE_INVALID')
        if value_type == 'dword' and (not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF):
            raise ValueError('REGISTRY_VALUE_INVALID')
        return {'path': _registry_path(params['path']), 'name': name,
                'value': value, 'value_type': value_type}
    raise ValueError('OPERATION_NOT_ALLOWED')

def dispatch_operation(operation: str, params: dict, backend):
    normalized = validate_operation(operation, params)
    if operation == 'identity.get':
        return backend.identity_get()
    if operation == 'service.get':
        return backend.service_get(normalized['name'])
    if operation == 'service.restart':
        return backend.service_restart(normalized['name'])
    if operation == 'task.get':
        return backend.task_get(normalized['name'])
    if operation == 'task.run':
        return backend.task_run(normalized['name'])
    if operation == 'file.write':
        return backend.file_write(normalized['path'], normalized['text'])
    if operation == 'registry.set':
        return backend.registry_set(normalized['path'], normalized['name'],
                                    normalized['value'], normalized['value_type'])
    raise ValueError('OPERATION_NOT_ALLOWED')