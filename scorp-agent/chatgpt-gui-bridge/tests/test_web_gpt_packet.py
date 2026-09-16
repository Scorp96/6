import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
TOOL = ROOT / 'scorp-agent' / 'chatgpt-gui-bridge' / 'tools' / 'v4_web_gpt_packet.py'


def load_tool():
    spec = importlib.util.spec_from_file_location('v4_web_gpt_packet_under_test', TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WebGptPacketTests(unittest.TestCase):
    def _current_release(self):
        paths = sorted((ROOT / 'docs' / 'handoffs').glob('SCORP_V4_RELEASE_RECORD_*.json'))
        self.assertEqual(1, len(paths), paths)
        path = paths[0]
        return path, json.loads(path.read_text(encoding='utf-8'))

    def test_packet_prefers_unique_current_release_record(self):
        release_path, release = self._current_release()
        validation_path = ROOT / release['validation_record']['path']
        validation = json.loads(validation_path.read_text(encoding='utf-8'))
        packet = load_tool().build_packet(ROOT)
        self.assertEqual(release['code_candidate_sha'], packet['candidate_commit'])
        self.assertEqual(
            release['validation_record']['path'],
            packet['evidence_binding']['validation_record'],
        )
        self.assertEqual(
            release['candidate_manifest']['path'],
            packet['evidence_binding']['candidate_manifest'],
        )

    def test_packet_carries_current_live_gate_from_validation_record(self):
        release_path, release = self._current_release()
        validation_path = ROOT / release['validation_record']['path']
        validation = json.loads(validation_path.read_text(encoding='utf-8'))
        live = validation['live']
        status = {
            'PASS': 'PASS',
            'BLOCKED': 'BLOCKED_EXTERNAL_PRECONDITION',
            'FAIL': 'FAIL',
        }.get(str(live.get('status') or 'NOT_RECORDED').upper(), str(live.get('status') or 'NOT_RECORDED').upper())
        expected_gate = {
            'candidate_code_commit': validation['candidate_commit'],
            'status': status,
            'reason': live['reason'],
            'evidence_path': live.get('evidence_path'),
            'retry_count_after_ambiguity': 0,
            'rule': 'Do not retry until read-only browser evidence shows the blocker cleared.',
        }
        packet = load_tool().build_packet(ROOT)
        gate = packet.get('current_live_gate')
        self.assertIsInstance(gate, dict)
        self.assertEqual(gate, expected_gate)


if __name__ == '__main__':
    unittest.main()
