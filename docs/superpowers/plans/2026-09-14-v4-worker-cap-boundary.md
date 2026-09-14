# V4 Worker Capacity Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the V4 browser gateway fail closed when a caller requests more than the first-release limit of two dynamic Workers, while preserving the legacy V3 review-swarm compatibility path for a separate migration task.

**Architecture:** `V4BridgeGateway` owns a V4 `Scheduler` configured for exactly two Worker slots. The gateway rejects an oversized caller request before touching SQLite or claiming assignments. Legacy V3 runtime defaults remain unchanged in this focused change and are explicitly treated as a separate migration boundary.

**Tech Stack:** Python 3, SQLite-backed V4 scheduler, `unittest`, existing Git6 candidate package.

**Spec:** `docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml`, sections `dynamic_workers.concurrency` and `architecture.layers`.

## Global Constraints

- V4 first release runs on one Windows host and allows exactly two Worker slots.
- Automatic capacity increase is forbidden.
- A rejected request must not claim a lease or mutate authoritative state.
- Legacy V3 JSON runtime remains compatibility-only until a separate migration is accepted.
- Do not modify dirty historical worktrees or production installation paths.

---

### Task 1: Add a failing oversized-limit regression test

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/tests/test_v4_bridge_gateway.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_v4_bridge_gateway.py`

**Interfaces:**
- Consumes: `V4BridgeGateway.claim_workers(limit=...)`.
- Produces: A regression assertion requiring `SchedulerError("V4_WORKER_LIMIT_INVALID")` for `limit > 2`.

- [x] Add a test that creates a valid V4 gateway and calls `claim_workers(limit=3)` before any graph is claimed.
- [x] Assert the exact error and verify the SQLite project state version is unchanged.
- [x] Run the focused test and observe the expected failure because the current gateway silently truncates the request.

### Task 2: Implement the minimal V4 gateway guard

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py`

**Interfaces:**
- Consumes: the existing `claim_workers` method and `SchedulerError`.
- Produces: fail-closed validation before `Scheduler.claim_runnable`.

- [x] Reject `limit > 2` with `SchedulerError("V4_WORKER_LIMIT_INVALID")`.
- [x] Preserve `limit <= 2` behavior and the existing default path.
- [x] Do not alter V3 JSON runtime defaults in this task.

### Task 3: Verify and publish the focused fix

**Files:**
- No additional source files.

- [x] Run the focused V4 gateway test.
- [x] Run all V4 tests, all GUI Bridge tests, and compileall.
- [x] Run `git diff --check` and a secret-pattern scan.
- [ ] Commit the isolated change with a focused message.
- [ ] Push only the Git6 `main` branch after verification; do not change `Scorp96/666` or production installation.

### Follow-up task: V3-to-V4 authority migration

This plan deliberately does not change `ProductionV3Runtime`, `WorkerConversationPoolV3`, or the JSON state files. A separate plan must first define the migration and prove that V4 SQLite is the sole mutable authority before changing those compatibility paths.
