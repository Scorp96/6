from __future__ import annotations

import json
import pathlib
import unittest

from master_a_dynamic_v4.runtime_connector_descriptor import (
    ConnectorDescriptorError,
    load_connector_descriptor,
    validate_connector_descriptor,
)
from master_a_dynamic_v4.runtime_protocol import ALL_COMMANDS


ROOT = pathlib.Path(__file__).resolve().parents[3]
DESCRIPTOR = ROOT / "docs" / "handoffs" / "SCORP_V4_RUNTIME_CONNECTOR_DESCRIPTOR.json"


class RuntimeConnectorDescriptorTests(unittest.TestCase):
    def test_repository_descriptor_is_bounded_and_explicitly_unregistered(self):
        descriptor = load_connector_descriptor(DESCRIPTOR)
        self.assertEqual("scorp.runtime.connector/1", descriptor["format"])
        self.assertEqual("UNREGISTERED_HOST", descriptor["registration"]["status"])
        self.assertTrue(descriptor["security"]["actor_binding_required"])
        self.assertTrue(descriptor["security"]["project_scope_required"])
        self.assertFalse(descriptor["security"]["retry_on_timeout"])
        self.assertEqual(
            {
                "runtime.status",
                "runtime.snapshot",
                "project.status",
                "master.status",
                "worker.status",
                "evidence.query",
                "project.pause",
                "project.resume",
                "project.cancel",
                "project.supersede",
                "project.emergency_stop",
            },
            set(descriptor["commands"]),
        )

    def test_unknown_or_actuator_command_is_rejected(self):
        with self.assertRaisesRegex(ConnectorDescriptorError, "COMMAND_NOT_ALLOWED"):
            validate_connector_descriptor(
                {
                    "format": "scorp.runtime.connector/1",
                    "protocol": "scorp.runtime.command/1",
                    "registration": {
                        "status": "UNREGISTERED_HOST",
                        "requires_explicit_host_registration": True,
                    },
                    "commands": ["runtime.status", "browser.send"],
                    "security": {
                        "actor_binding_required": True,
                        "project_scope_required": True,
                        "retry_on_timeout": False,
                        "arbitrary_shell": False,
                        "arbitrary_browser": False,
                        "arbitrary_filesystem": False,
                        "arbitrary_git": False,
                    },
                    "transport": {"max_message_bytes": 65536, "authkey_env": "SCORP_RUNTIME_PIPE_AUTHKEY"},
                }
            )

    def test_retry_or_missing_scope_fails_closed(self):
        with self.assertRaisesRegex(ConnectorDescriptorError, "RETRY_POLICY_INVALID"):
            validate_connector_descriptor(
                {
                    "format": "scorp.runtime.connector/1",
                    "protocol": "scorp.runtime.command/1",
                    "registration": {
                        "status": "UNREGISTERED_HOST",
                        "requires_explicit_host_registration": True,
                    },
                    "commands": sorted(ALL_COMMANDS),
                    "security": {
                        "actor_binding_required": True,
                        "project_scope_required": True,
                        "retry_on_timeout": True,
                        "arbitrary_shell": False,
                        "arbitrary_browser": False,
                        "arbitrary_filesystem": False,
                        "arbitrary_git": False,
                    },
                    "transport": {"max_message_bytes": 65536, "authkey_env": "SCORP_RUNTIME_PIPE_AUTHKEY"},
                }
            )


if __name__ == "__main__":
    unittest.main()
