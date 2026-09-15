from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import unittest
import uuid
from multiprocessing.connection import Client

from master_a_dynamic_v4.runtime_commands import RuntimeCommandService
from master_a_dynamic_v4.runtime_pipe import (
    MAX_MESSAGE_BYTES,
    RuntimePipeServer,
    create_listener,
    pipe_name,
)
from master_a_dynamic_v4.state_store import StateStore


class RuntimePipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = StateStore(self.root / "runtime.sqlite", [self.root])
        self.store.create_contract(
            "p1", root_contract={"objective": "demo"}, acceptance_contract={"required": []}
        )
        lease = self.store.acquire_daemon_lease("p1", "daemon-1")
        self.service = RuntimeCommandService(
            self.store, project_id="p1", daemon_epoch=lease["daemon_epoch"], actor="pipe"
        )
        self.authkey = b"scorp-test-authkey"

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def request(self, command="runtime.status"):
        return json.dumps(
            {
                "protocol_version": "scorp.runtime.command/1",
                "request_id": "pipe-1",
                "command": command,
                "project_id": "p1",
                "actor": "gpt-master",
                "payload": {},
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def test_pipe_name_is_fixed_and_project_scoped(self):
        self.assertEqual(r"\\.\pipe\scorp-runtime-p1", pipe_name("p1"))
        with self.assertRaisesRegex(ValueError, "PIPE_PROJECT_ID_INVALID"):
            pipe_name("p1/../other")
        with self.assertRaisesRegex(ValueError, "PIPE_PROJECT_ID_INVALID"):
            pipe_name(" ")

    def test_handle_bytes_delegates_only_versioned_runtime_request(self):
        server = RuntimePipeServer(self.service, project_id="p1", authkey=self.authkey, actor="gpt-master")
        response = json.loads(server.handle_bytes(self.request()))
        self.assertEqual("scorp.runtime.response/1", response["protocol_version"])
        self.assertEqual("OK", response["status"])
        self.assertEqual("runtime.status", response["command"])

    def test_handle_bytes_rejects_oversized_message_without_dispatch(self):
        server = RuntimePipeServer(self.service, project_id="p1", authkey=self.authkey, actor="gpt-master")
        response = json.loads(server.handle_bytes(b"x" * (MAX_MESSAGE_BYTES + 1)))
        self.assertEqual("REJECTED", response["status"])
        self.assertEqual("PIPE_MESSAGE_TOO_LARGE", response["error"]["code"])

    def test_handle_bytes_rejects_actor_impersonation(self):
        server = RuntimePipeServer(
            self.service,
            project_id="p1",
            authkey=self.authkey,
            actor="gpt-master",
        )
        raw = json.loads(self.request().decode("utf-8"))
        raw["actor"] = "untrusted-local-client"
        response = json.loads(server.handle_bytes(json.dumps(raw).encode("utf-8")))
        self.assertEqual("REJECTED", response["status"])
        self.assertEqual("ACTOR_SCOPE_MISMATCH", response["error"]["code"])

    def test_serve_once_closes_connection_when_transport_rejects_oversized_frame(self):
        server = RuntimePipeServer(self.service, project_id="p1", authkey=self.authkey, actor="gpt-master")

        class Connection:
            def __init__(self):
                self.closed = False
                self.sent = []

            def recv_bytes(self, _max_length):
                raise OSError("message too long")

            def send_bytes(self, payload):
                self.sent.append(payload)

            def close(self):
                self.closed = True

        class Listener:
            def __init__(self, connection):
                self.connection = connection

            def accept(self):
                return self.connection

        connection = Connection()
        # A malformed/oversized frame has no trustworthy request identity. The
        # server must fail closed by closing that connection, not by killing
        # the listener loop or manufacturing a success response.
        server.serve_once(Listener(connection))
        self.assertTrue(connection.closed)
        self.assertEqual([], connection.sent)

    @unittest.skipUnless(os.name == "nt", "Windows Named Pipe transport")
    def test_real_named_pipe_round_trip_uses_authenticated_connection(self):
        scoped_project = "p1-" + uuid.uuid4().hex[:12]
        self.store.create_contract(
            scoped_project,
            root_contract={"objective": "pipe"},
            acceptance_contract={"required": []},
        )
        lease = self.store.acquire_daemon_lease(scoped_project, "daemon-pipe")
        service = RuntimeCommandService(
            self.store, project_id=scoped_project, daemon_epoch=lease["daemon_epoch"], actor="pipe"
        )
        server = RuntimePipeServer(
            service,
            project_id=scoped_project,
            authkey=self.authkey,
            actor="gpt-master",
        )
        listener = create_listener(server.endpoint, authkey=self.authkey)
        failure: list[BaseException] = []

        def accept_once():
            try:
                server.serve_once(listener)
            except BaseException as exc:  # pragma: no cover - surfaced below
                failure.append(exc)

        thread = threading.Thread(target=accept_once, daemon=True)
        thread.start()
        client = Client(server.endpoint, family="AF_PIPE", authkey=self.authkey)
        try:
            request = json.loads(self.request().decode("utf-8"))
            request["project_id"] = scoped_project
            request["request_id"] = "pipe-real-1"
            client.send_bytes(json.dumps(request, separators=(",", ":")).encode("utf-8"))
            response = json.loads(client.recv_bytes().decode("utf-8"))
        finally:
            client.close()
            thread.join(timeout=5)
            listener.close()
        self.assertFalse(failure, failure)
        self.assertEqual("OK", response["status"])
        self.assertEqual(scoped_project, response["project_id"])


if __name__ == "__main__":
    unittest.main()
