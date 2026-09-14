from __future__ import annotations

import unittest

from master_a_dynamic_v4.runtime_protocol import (
    MUTATION_COMMANDS,
    READ_COMMANDS,
    RuntimeProtocolError,
    build_response,
    parse_request,
)


class RuntimeProtocolTests(unittest.TestCase):
    def test_parse_valid_request_and_hash_payload_deterministically(self):
        request = parse_request(
            {
                "protocol_version": "scorp.runtime.command/1",
                "request_id": "r-1",
                "command": "runtime.status",
                "project_id": "p",
                "payload": {"b": 2, "a": 1},
            }
        )
        self.assertEqual("runtime.status", request.command)
        self.assertEqual(request.payload_sha256, parse_request({
            "protocol_version": "scorp.runtime.command/1",
            "request_id": "r-2",
            "command": "runtime.status",
            "project_id": "p",
            "payload": {"a": 1, "b": 2},
        }).payload_sha256)
        self.assertIn("runtime.status", READ_COMMANDS)
        self.assertIn("project.pause", MUTATION_COMMANDS)

    def test_mutation_requires_cas_fields(self):
        with self.assertRaisesRegex(RuntimeProtocolError, "EXPECTED_STATE_VERSION_REQUIRED"):
            parse_request({
                "protocol_version": "scorp.runtime.command/1",
                "request_id": "r-1",
                "command": "project.pause",
                "project_id": "p",
                "payload": {},
            })

    def test_unknown_fields_and_shell_commands_fail_closed(self):
        base = {
            "protocol_version": "scorp.runtime.command/1",
            "request_id": "r-1",
            "command": "runtime.status",
            "project_id": "p",
            "payload": {},
        }
        with self.assertRaisesRegex(RuntimeProtocolError, "UNKNOWN_REQUEST_FIELD"):
            parse_request({**base, "shell": "whoami"})
        with self.assertRaisesRegex(RuntimeProtocolError, "UNKNOWN_COMMAND"):
            parse_request({**base, "command": "powershell.exe"})

    def test_response_is_versioned_and_machine_readable(self):
        request = parse_request({
            "protocol_version": "scorp.runtime.command/1",
            "request_id": "r-1",
            "command": "runtime.status",
            "project_id": "p",
            "payload": {},
        })
        response = build_response(
            request,
            status="OK",
            daemon_epoch=4,
            state_version=7,
            result={"ready": True},
        )
        self.assertEqual("scorp.runtime.response/1", response["protocol_version"])
        self.assertEqual("r-1", response["request_id"])
        self.assertEqual("OK", response["status"])
        self.assertIsNone(response["error"])
        self.assertEqual({"ready": True}, response["result"])


if __name__ == "__main__":
    unittest.main()
