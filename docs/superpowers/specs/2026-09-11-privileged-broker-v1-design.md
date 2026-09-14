# Privileged Broker V1 Design

## Goal
Add a local, deterministic Windows privilege boundary for the Scorp agent stack. The broker runs as `LocalSystem`, accepts only authenticated local requests, executes a small allowlist of privileged operations, and never performs reasoning.

## Position in the Architecture
`GPT-5.6 Sol -> Orchestrator -> V4 -> Privileged Broker -> Windows`.

The existing Orchestrator and V4 remain authoritative for task identity, exactly-once task execution, and safety classification. The broker adds a second, narrower exactly-once boundary for privileged effects. Desktop and browser agents remain separate; this release does not build the unified Agent Router yet.

## Runtime
Use Python 3.14 plus pinned `pywin32`. Install a dedicated runtime at `C:\ScorpAgent\privileged-broker-runtime` and service code at `C:\ScorpAgent\privileged-broker`. The Windows service name is `ScorpPrivilegedBroker` and runs as `LocalSystem`, automatic start.
## Transport and Authentication
The broker listens only on a local Windows named pipe: `\\.\pipe\ScorpPrivilegedBrokerV1`. The pipe ACL grants full access to `SYSTEM`, Administrators, and the installing user SID; no network transport is exposed.

Each request is one UTF-8 JSON object with `protocol_version`, `request_id`, `operation`, `params`, `issued_at`, `expires_at`, and `auth`. `auth` is an HMAC-SHA256 over canonical compact JSON of all fields except `auth` using a 32-byte secret generated at install time.

The secret is stored under `C:\ProgramData\ScorpAgent\privileged-broker\secret.key`, with ACL restricted to `SYSTEM`, Administrators, and the installing user. Unknown protocol versions, invalid HMACs, expired requests, malformed parameters, and unauthorized operations fail closed.
## Exactly-Once and Audit
Before executing an operation, the service records `request_id` as inflight in an atomic ledger under `C:\ProgramData\ScorpAgent\privileged-broker\ledger.json`. A completed request stores the complete result and result SHA-256. Replaying the same `request_id` with the same canonical request returns the stored result without executing again. Reusing a `request_id` with different content is rejected as a conflict.

Every accepted or rejected request appends one JSON line to `audit.jsonl` containing timestamps, request id, operation, request hash, status, result hash when present, and service identity. Secrets and raw HMAC keys never enter logs.

## V1 Operations
- `identity.get`: return Windows identity, PID, session id, and whether the service is LocalSystem.
- `service.get`: query one Windows service.
- `service.restart`: restart only service names beginning with `Scorp`; reject the broker service itself in V1.
- `task.get`: query one Scheduled Task whose name begins with `Scorp`.
- `task.run`: start one Scheduled Task whose name begins with `Scorp`.
- `file.write`: write UTF-8 text only beneath `C:\ProgramData\ScorpAgent\broker-data\` using atomic replace.
- `registry.set`: set string/DWORD values only beneath `HKLM\Software\ScorpAgent\`.

No arbitrary PowerShell, CMD, executable path, script body, remote URL, process injection, credential operation, security-policy mutation, account mutation, or unrestricted filesystem/registry operation is accepted.
## Client
`broker_client.py` is a deterministic local client used by V4 and tests. It loads the shared secret, builds canonical requests, signs them, writes one request to the named pipe, reads one response, validates protocol/request identity, and exits nonzero on broker errors.

The client may generate a UUID request id when used manually, but V4 integration will pass an identity-bound request id derived from the V4 action. This release does not yet modify the V4 executor; broker acceptance is completed first as an isolated subsystem.

## Service Lifecycle and Rollback
`install-broker.ps1` creates the runtime, pins pywin32, generates or preserves the secret, installs the Windows service, starts it, and verifies `identity.get`. Existing installation files and service configuration are backed up before switch. Installation fails closed and restores the previous service/files when verification fails.

`uninstall-broker.ps1` is not invoked automatically. It stops/removes only `ScorpPrivilegedBroker` and preserves ledger/audit data unless the user separately authorizes data deletion.

## Acceptance
Unit tests must cover canonical signing, invalid/expired auth, request-id conflicts, replay caching, path/registry/service/task allowlists, and operation dispatch. Production acceptance requires: service status Running; start account LocalSystem; client `identity.get` reports `NT AUTHORITY\SYSTEM`; the same signed request id replay returns byte-equivalent stored result; a conflicting replay is rejected; audit contains one execution plus replay evidence; service restart preserves ledger and exactly-once behavior.