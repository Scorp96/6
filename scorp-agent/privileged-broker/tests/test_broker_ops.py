import unittest

from broker_ops import dispatch_operation, validate_operation


class FakeBackend:
    def __init__(self):
        self.calls = []

    def identity_get(self):
        self.calls.append(('identity.get', {}))
        return {'identity': 'SYSTEM'}

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
        return {'path': path, 'name': name}


class BrokerOpsTests(unittest.TestCase):
    def test_identity_dispatch(self):
        backend = FakeBackend()
        result = dispatch_operation('identity.get', {}, backend)
        self.assertEqual(result['identity'], 'SYSTEM')
        self.assertEqual(backend.calls, [('identity.get', {})])

    def test_service_restart_requires_scorp_and_rejects_self(self):
        with self.assertRaisesRegex(ValueError, 'SERVICE_NAME_NOT_ALLOWED'):
            validate_operation('service.restart', {'name': 'Spooler'})
        with self.assertRaisesRegex(ValueError, 'BROKER_SELF_RESTART_REJECTED'):
            validate_operation('service.restart', {'name': 'ScorpPrivilegedBroker'})
        self.assertEqual(validate_operation('service.restart', {'name': 'ScorpExample'})['name'], 'ScorpExample')

    def test_task_operations_require_scorp_prefix(self):
        for op in ('task.get', 'task.run'):
            with self.assertRaisesRegex(ValueError, 'TASK_NAME_NOT_ALLOWED'):
                validate_operation(op, {'name': '\\Microsoft\\Windows\\Defrag\\ScheduledDefrag'})
            self.assertEqual(validate_operation(op, {'name': 'ScorpComputerAgent'})['name'], 'ScorpComputerAgent')

    def test_file_write_rejects_escape_and_absolute_other_root(self):
        good = validate_operation('file.write', {'path': 'reports\\a.txt', 'text': 'ok'})
        self.assertTrue(good['path'].lower().startswith('c:\\programdata\\scorpagent\\broker-data\\'))
        for bad in ('..\\escape.txt', r'C:\Windows\Temp\x.txt', r'\\server\share\x.txt'):
            with self.assertRaisesRegex(ValueError, 'FILE_PATH_NOT_ALLOWED'):
                validate_operation('file.write', {'path': bad, 'text': 'x'})

    def test_registry_scope_and_types_are_restricted(self):
        good = validate_operation('registry.set', {'path': r'HKLM\Software\ScorpAgent\Broker', 'name': 'Mode', 'value': 'on', 'value_type': 'string'})
        self.assertEqual(good['value_type'], 'string')
        with self.assertRaisesRegex(ValueError, 'REGISTRY_PATH_NOT_ALLOWED'):
            validate_operation('registry.set', {'path': r'HKLM\Software\Microsoft\Windows', 'name': 'X', 'value': 1, 'value_type': 'dword'})
        with self.assertRaisesRegex(ValueError, 'REGISTRY_TYPE_NOT_ALLOWED'):
            validate_operation('registry.set', {'path': r'HKLM\Software\ScorpAgent', 'name': 'X', 'value': 'x', 'value_type': 'binary'})

    def test_unknown_or_shell_like_operation_rejected(self):
        for op in ('powershell', 'cmd.exec', 'process.run'):
            with self.assertRaisesRegex(ValueError, 'OPERATION_NOT_ALLOWED'):
                validate_operation(op, {'command': 'whoami'})

    def test_dispatch_uses_normalized_params(self):
        backend = FakeBackend()
        result = dispatch_operation('file.write', {'path': 'x.txt', 'text': 'abc'}, backend)
        self.assertEqual(result['bytes'], 3)
        op, params = backend.calls[-1]
        self.assertEqual(op, 'file.write')
        self.assertTrue(params['path'].lower().endswith('broker-data\\x.txt'))


if __name__ == '__main__':
    unittest.main()

