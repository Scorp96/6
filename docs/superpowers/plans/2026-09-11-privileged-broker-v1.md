# Privileged Broker V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install a LocalSystem named-pipe broker that exposes a small authenticated, exactly-once allowlist of privileged Windows operations.

**Architecture:** Pure protocol/auth/ledger/allowlist logic lives in testable Python modules. A pywin32 Windows Service hosts a local named-pipe server; a deterministic client signs requests with a local shared secret. Installer creates a dedicated runtime and service and verifies identity before declaring success.

**Tech Stack:** Python 3.14, pywin32, unittest, Windows Service Control Manager, Windows named pipes, PowerShell 5.1 installer.

**Spec:** `docs/superpowers/specs/2026-09-11-privileged-broker-v1-design.md`

## Global Constraints
- No GPT/Codex/local model in the broker process.
- No arbitrary shell/PowerShell/executable operation.
- Local named pipe only; no TCP listener.
- HMAC-SHA256, expiry, request hash, exactly-once ledger, append-only audit.
- Service account must be LocalSystem.
- V1 does not modify V4 integration yet.

---
### Task 1: Protocol, authentication, and replay ledger

**Files:**
- Create: `scorp-agent/privileged-broker/broker_core.py`
- Create: `scorp-agent/privileged-broker/tests/test_broker_core.py`

**Interfaces:**
- Produces: `canonical_request_bytes(request)`, `sign_request(request, secret)`, `validate_request(request, secret, now)`, `RequestLedger`.

- [ ] **Step 1:** Write failing tests for canonical HMAC, invalid HMAC, expiry, same-request replay, conflicting request-id reuse, and atomic ledger persistence.
- [ ] **Step 2:** Run targeted tests and verify RED reaches the intended missing symbols/assertions.
- [ ] **Step 3:** Implement the minimal pure-Python protocol and ledger code.
- [ ] **Step 4:** Run targeted tests to GREEN.
- [ ] **Step 5:** Commit `feat: add privileged broker protocol core`.
### Task 2: Privileged operation allowlist

**Files:**
- Create: `scorp-agent/privileged-broker/broker_ops.py`
- Create: `scorp-agent/privileged-broker/tests/test_broker_ops.py`

**Interfaces:**
- Consumes: validated `operation` plus `params`.
- Produces: deterministic operation result dictionaries for identity, service, task, file, and registry operations.

- [ ] **Step 1:** Write failing tests for allowed prefixes/roots and rejection of self-restart, arbitrary executable/shell input, path traversal, and registry escape.
- [ ] **Step 2:** Run targeted tests and capture valid RED.
- [ ] **Step 3:** Implement operation validation plus injectable Windows backends so pure policy tests do not mutate the host.
- [ ] **Step 4:** Run targeted tests and the Task 1 suite.
- [ ] **Step 5:** Commit `feat: add privileged broker operation allowlist`.
### Task 3: Named-pipe service and client

**Files:**
- Create: `scorp-agent/privileged-broker/broker_service.py`
- Create: `scorp-agent/privileged-broker/broker_client.py`
- Create: `scorp-agent/privileged-broker/tests/test_broker_service.py`

**Interfaces:**
- Service consumes one signed JSON request per named-pipe connection and returns one JSON response.
- Client exposes deterministic request construction/signing and pipe round-trip.

- [ ] **Step 1:** Add failing tests for request framing, auth/dispatch/replay flow, response identity, and client validation using an in-memory handler boundary.
- [ ] **Step 2:** Run targeted tests and verify RED.
- [ ] **Step 3:** Implement the request handler, pywin32 service host, pipe ACL, and deterministic client.
- [ ] **Step 4:** Run all broker tests to GREEN and `py_compile` all broker Python files.
- [ ] **Step 5:** Commit `feat: add LocalSystem broker service and client`.
### Task 4: Install, rollback, and production acceptance

**Files:**
- Create: `scorp-agent/privileged-broker/install-broker.ps1`
- Create: `scorp-agent/privileged-broker/uninstall-broker.ps1`
- Create: `scorp-agent/privileged-broker/tests/test_install_contract.py`

**Interfaces:**
- Installer creates the dedicated runtime/install/state roots, preserves state on upgrade, installs `ScorpPrivilegedBroker` as LocalSystem/Automatic, starts it, and verifies via the client.

- [ ] **Step 1:** Add static contract tests for fixed paths, pinned pywin32, LocalSystem service install, state preservation, rollback, and identity verification.
- [ ] **Step 2:** Run targeted tests to valid RED, implement installer/uninstaller, then run the full broker suite.
- [ ] **Step 3:** Inspect diff/status, push the feature branch, and install on Scorp with rollback capture.
- [ ] **Step 4:** Verify Running/LocalSystem, `identity.get`, same-request replay, conflicting replay rejection, audit evidence, and service-restart persistence.
- [ ] **Step 5:** If all gates pass, mark Privileged Broker V1 GREEN and only then design the V4/Agent Router integration.