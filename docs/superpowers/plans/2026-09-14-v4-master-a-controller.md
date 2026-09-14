# V4 Master A Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a deterministic, model-agnostic Master A controller that turns a structured GPT plan into durable SQLite contracts, two bounded browser Worker assignments, verified results, recovery decisions, and an independent completion decision.

**Architecture:** `MasterAController` is a thin orchestration layer over the existing `V4BridgeGateway`. GPT supplies only validated structured plans and Worker response payloads through injected callbacks; the controller owns session fencing, claim recovery, browser intent creation, result validation, and completion checks. No model API, cloud service, legacy JSON authority, or production installation is added.

**Tech Stack:** Python 3 standard library, existing `V4BridgeGateway`, SQLite `StateStore`, `Scheduler`, `BrowserAdapter`, `AcceptanceValidator`, and `unittest`.

**Spec:** `docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml`, especially `master_A`, `dynamic_workers`, `contracts`, `browser_transport`, and `completion_gate`.

## Global Constraints

- Master identity is always `A`; a Worker cannot change the root contract or capacity.
- Worker concurrency is exactly two; requests above two are rejected before state mutation.
- SQLite remains the only mutable authority; no database transaction may contain browser or network I/O.
- Every browser send is preceded by a durable intent and ambiguous results remain blocked until read-only reconciliation.
- Every accepted Worker result is a `WORK_RESULT/1` payload bound to project, task, assignment, objective hash, and base state version.
- `BLOCKED`, `FAIL`, and `NOT_RUN` are never converted to `PASS`.
- Tests use injected fake browser engines; no new live messages are sent during implementation.
- No scheduled task, service, production state root, browser profile, or legacy JSON file is modified.

---

### Task 1: Define the controller result and callback boundary

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/master_controller.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_master_controller.py`

**Interfaces:**
- `MasterAController(gateway, session_id)` owns one logical Master session.
- `start(root_contract, acceptance_contract) -> dict` creates/verifies the contract and starts a fenced session.
- `apply_plan(plan) -> dict` validates `project_id`, `master_identity`, task IDs, hashes, paths, dependencies, and acceptance IDs, then persists the graph.
- `step(worker_prompt_factory, worker_response_decoder) -> ControllerStep` recovers active claims, claims at most two runnable assignments, persists/submits each intent through the gateway, decodes only structured `WORK_RESULT/1`, verifies it, and returns per-task outcomes.
- `watchdog_once() -> dict` delegates the liveness decision without browser I/O.
- `completion(candidate_commit, artifact_hashes) -> AcceptanceDecision` delegates the independent read-only validator.

- [ ] **Step 1: Write failing tests**

  Add tests that import `MasterAController` before it exists and assert:

  ```python
  controller.apply_plan({"project_id": "p", "master_identity": "B", "tasks": []})
  # raises ControllerRejected("MASTER_IDENTITY_INVALID")
  ```

  Add a second test describing the desired happy-path return shape: `step()` must report two distinct assignment IDs and never claim a third slot.

- [ ] **Step 2: Run the focused tests and verify the expected missing-module failure**

  Run from the repository root:

  ```powershell
  $env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
  python -B -m unittest discover -s scorp-agent/master_a_dynamic_v4/tests -p 'test_master_controller.py'
  ```

- [ ] **Step 3: Implement the minimal boundary and immutable controller step record**

  Use dataclasses for `ControllerStep` and `ControllerRejected`. Normalize plan mappings without allowing unknown authority fields to mutate SQLite. `start()` must be idempotent for the same contract and session, while a different contract or active session is rejected by the existing store.

- [ ] **Step 4: Run the focused tests and confirm the boundary is green**

- [ ] **Step 5: Commit the controller boundary**

  ```powershell
  git add scorp-agent/master_a_dynamic_v4/master_controller.py scorp-agent/master_a_dynamic_v4/tests/test_master_controller.py
  git commit -m "feat: add Master A controller boundary"
  ```

### Task 2: Implement plan validation and durable two-Worker dispatch

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/master_controller.py`
- Modify: `scorp-agent/master_a_dynamic_v4/tests/test_master_controller.py`

**Interfaces:**
- Plan task entries use the existing Scheduler fields: `task_id`, `objective_sha256`, `resource_scope`, `access_mode`, `dependencies`, and optional `acceptance_criteria_ids`.
- `worker_prompt_factory(claim) -> str` is pure and cannot write state.
- `worker_response_decoder(intent_row) -> Mapping` receives only a captured response and must return a `WORK_RESULT/1` mapping or raise `ControllerRejected`.

- [ ] **Step 1: Add failing tests for dependency ordering, scope, and two-slot dispatch**

  Assert T1/T2 are claimed together, T3 remains queued until both structured results verify, a third requested claim is rejected, and a stale/foreign response is rejected before task state changes.

- [ ] **Step 2: Run the focused tests and record the expected failures**

- [ ] **Step 3: Implement `apply_plan()` and `step()`**

  `step()` must call `load_worker_claims()` before claiming new work, submit no more than the free capacity, persist assignment-bound prompts with `submit_worker_intent()`, inspect the durable intent row for `RESPONSE_CAPTURED`, decode and verify the structured result, and leave `MAY_HAVE_SUBMITTED`/`BLOCKED_AMBIGUOUS` untouched for `recover()` rather than retrying. A browser response that is absent, malformed, or not `WORK_RESULT/1` becomes a deterministic blocker.

- [ ] **Step 4: Run the focused tests and the existing V4 suite**

  ```powershell
  $env:PYTHONPATH = (Join-Path $PWD 'scorp-agent')
  python -B -m unittest discover -s scorp-agent/master_a_dynamic_v4/tests -p 'test_*.py'
  ```

- [ ] **Step 5: Commit the dispatch implementation**

  ```powershell
  git add scorp-agent/master_a_dynamic_v4/master_controller.py scorp-agent/master_a_dynamic_v4/tests/test_master_controller.py
  git commit -m "feat: dispatch and verify dynamic Master A workers"
  ```

### Task 3: Add restart/watchdog and completion behavior

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/master_controller.py`
- Modify: `scorp-agent/master_a_dynamic_v4/tests/test_master_controller.py`

**Interfaces:**
- `resume()` reuses the current session epoch, loads active claims, and never creates a duplicate browser intent.
- `watchdog_once()` returns `MASTER_ACTIVE`, `RESUME_REQUIRED`, or `TERMINAL` exactly as the existing `MasterWatchdog`.
- `completion()` returns the existing `AcceptanceDecision` and never writes a completion flag.

- [ ] **Step 1: Add failing restart and false-completion tests**

  Simulate an expired Master session and an unresolved browser intent; assert the controller returns a resume/blocker decision and does not claim new work. Simulate a GPT `COMPLETE` response with missing evidence; assert `completion()` remains `BLOCKED`.

- [ ] **Step 2: Run tests and verify the expected failures**

- [ ] **Step 3: Implement restart, heartbeat, and completion delegation**

  Resume uses the gateway’s durable APIs and preserves the old epoch as fenced. `completion()` passes through the validator result without interpreting natural language.

- [ ] **Step 4: Run all V4 tests and compile the package**

- [ ] **Step 5: Commit the lifecycle behavior**

  ```powershell
  git add scorp-agent/master_a_dynamic_v4/master_controller.py scorp-agent/master_a_dynamic_v4/tests/test_master_controller.py
  git commit -m "feat: recover Master A and preserve completion gates"
  ```

### Task 4: Document ordinary-GPT handoff and refresh validation evidence

**Files:**
- Modify: `GPT_START_HERE.md`
- Modify: `README.md`
- Modify: `docs/handoffs/SCORP_V4_GIT6_HANDOFF.md`
- Modify: `docs/handoffs/SCORP_V4_GIT6_VALIDATION.json`

- [ ] **Step 1: Add documentation tests or fixed-string checks first**

  Assert the handoff names `MasterAController`, its structured plan boundary, the two-Worker limit, and the fact that browser side effects still require an authenticated session and explicit canary authorization.

- [ ] **Step 2: Update the handoff**

  Include a copyable sequence: `start -> apply_plan -> step -> watchdog_once -> completion`, plus the exact current Git commit and the distinction between offline controller tests and live browser evidence.

- [ ] **Step 3: Run full validation and update the machine-readable record**

  Record the final code commit, V4 test count, bridge test count, broker count, compile result, and unchanged production-cutover status. Do not overwrite historical evidence; add a new current record or update only the Git6 record.

- [ ] **Step 4: Commit and push Git6 `main`**

  ```powershell
  git add GPT_START_HERE.md README.md docs/handoffs/SCORP_V4_GIT6_HANDOFF.md docs/handoffs/SCORP_V4_GIT6_VALIDATION.json
  git commit -m "docs: hand off Master A controller workflow"
  git push origin HEAD:main
  ```

### Task 5: Final evidence audit

- [ ] Run `scripts/run-candidate-validation.ps1` from the exact worktree.
- [ ] Run `git diff --check`, parse every changed JSON file, and verify a clean tree.
- [ ] Confirm no browser message, production state, scheduled task, or credential was changed by implementation.
- [ ] Report the exact commit, files, test counts, live canary evidence, remaining unverified production boundaries, and the command ordinary GPT should use next.

