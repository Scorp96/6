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
    def test_packet_carries_current_live_gate_from_validation_record(self):
        validation_path = ROOT / 'docs' / 'handoffs' / 'SCORP_V4_GIT6_VALIDATION.json'
        validation = json.loads(validation_path.read_text(encoding='utf-8'))
        expected_gate = validation['current_candidate_live_gate']
        packet = load_tool().build_packet(ROOT)
        gate = packet.get('current_live_gate')
        self.assertIsInstance(gate, dict)
        self.assertEqual(gate, expected_gate)


if __name__ == '__main__':
    unittest.main()
