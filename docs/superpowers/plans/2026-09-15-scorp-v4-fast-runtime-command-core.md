# SCORP V4 Fast Local Runtime Command Core Implementation Plan

> **For agentic workers:** Execute this plan inline task-by-task with the TDD workflow. Each task ends with a focused test and a small commit.

**Goal:** Add a versioned, local, bounded, auditable Runtime Command Core over the existing SQLite/daemon/Master/Worker architecture.

**Architecture:** Keep `StateStore` as the durable authority and add focused protocol, command, operator-control, and CLI modules. Add only the tables and adapter gates needed for receipts, idempotency, operator generations, bounded observations, and fail-closed external side effects; preserve the existing browser and execution adapters.

**Tech Stack:** Python 3.14 runtime, SQLite with existing `StateStore`, `unittest`, JSON envelopes over stdin/stdout, Windows-compatible paths.

**Spec:** `docs/superpowers/specs/2026-09-15-scorp-v4-fast-runtime-command-core-design.md`

## Global Constraints

- Work only in `C:\ScorpAgent\worktrees\v4-fast-runtime-command-core` on `feature/v4-fast-runtime-command-core`.
- Do not reset, clean, stash, deploy, register scheduled tasks, or modify production state.
- Do not call paid model APIs or add cloud services.
- Do not expose arbitrary shell, PowerShell, Python eval, subprocess, Git, filesystem write, URL navigation, or browser click commands.
- All mutation success paths require a durable SQLite receipt before returning `OK`.
- `request_id`, `expected_state_version`, and `expected_daemon_epoch` are the CAS/idempotency boundary.
- `CANCELLED`, `SUPERSEDED`, and `PAUSED` generations must block new external side effects; ambiguous effects remain reconciliation-only.
- `IDLE` is not progress; real browser ambiguity, auth, CAPTCHA, and host blockers remain fail-closed.
- Unit/regression PASS never implies live browser or production acceptance.

### Task 1: Specify and validate the protocol envelope

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/runtime_protocol.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_runtime_protocol.py`

**Interfaces:**
- Produce `parse_request(raw: Mapping[str, Any]) -> RuntimeRequest`.
- Produce `build_response(request: RuntimeRequest, *, status: str, ...) -> dict[str, Any]`.
- Produce `protocol_error(request_id: str | None, ...) -> dict[str, Any]`.

- [ ] Write failing tests for valid envelopes, unknown commands, malformed protocol versions, unknown top-level fields, missing mutation CAS fields, deterministic payload hashing, and response field names.
- [ ] Run `python -B -m unittest discover -s scorp-agent/master_a_dynamic_v4/tests -p 'test_runtime_protocol.py'`; expect import/attribute failures because the module is absent.
- [ ] Implement only immutable command constants, request parsing, payload hash, and response construction. Reject shell-like command names and unknown fields before dispatch.
- [ ] Re-run the focused test and require all protocol cases to pass.
- [ ] Commit `test/feat: add runtime command protocol envelope`.

### Task 2: Add durable controls, observations, and receipts to StateStore

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/schema.sql`
- Modify: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_runtime_store.py`

**Interfaces:**
- `get_operator_control(project_id) -> dict[str, Any]`.
- `record_runtime_observation(project_id, observation) -> dict[str, Any]`.
- `get_runtime_observation(project_id) -> dict[str, Any]`.
- `get_command_receipt(request_id) -> dict[str, Any] | None`.
- `record_command_receipt(...) -> dict[str, Any]` with one transaction boundary.
- `runtime_snapshot(project_id, daemon_epoch) -> dict[str, Any]`.

- [ ] Write RED tests for control-row creation, receipt uniqueness, conflicting duplicate request IDs, observation timestamp persistence, and bounded snapshot counts.
- [ ] Run the focused store test and verify it fails for missing tables/methods.
- [ ] Add the three tables from the design and initialize `operator_controls` when `create_contract()` succeeds.
- [ ] Implement store methods with existing transaction/connection helpers; ensure `record_command_receipt` inserts the response and state transition atomically.
- [ ] Re-run focused store tests and the existing SQLite schema tests.
- [ ] Commit `feat: persist runtime controls observations and receipts`.

### Task 3: Implement read-only status and bounded evidence queries

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/runtime_commands.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_runtime_commands_read.py`

**Interfaces:**
- `RuntimeCommandService(store, project_id, actor_id).execute(request) -> dict[str, Any]`.
- Read dispatch for `runtime.status`, `project.status`, `master.status`, and `evidence.query`.
- Evidence query accepts `assignment_id`, `intent_id`, `receipt_id`, `from_utc`, `to_utc`, and `limit` (maximum 100).

- [ ] Write RED tests for each read command, bounded limit enforcement, and no mutation/receipt side effects from reads.
- [ ] Run the focused test and confirm missing service/command errors.
- [ ] Implement read dispatch using only bounded SQLite queries; never scan arbitrary log files.
- [ ] Re-run focused tests and assert the response envelope contains daemon epoch/state version and machine-readable results.
- [ ] Commit `feat: add bounded runtime status and evidence queries`.

### Task 4: Implement operator mutations, CAS, receipts, and idempotency

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/runtime_commands.py`
- Create: `scorp-agent/master_a_dynamic_v4/operator_control.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_operator_control.py`

**Interfaces:**
- `OperatorControl.apply(command, request) -> dict[str, Any]` for `project.pause`, `project.resume`, `project.cancel`, and `project.supersede`.
- Mutation result always includes `receipt_id`, input/output versions, epochs, operator/objective generations, result, and reason.

- [ ] Write RED tests for pause, resume, cancel, supersede, repeated identical request IDs, conflicting request IDs, stale state version, stale daemon epoch, and receipt persistence across StateStore reopen.
- [ ] Run the focused test and verify failures before production code exists.
- [ ] Implement one SQLite transaction per mutation: verify CAS/fence, update control/project state, fence old task/result authority, insert receipt, and return the stored response.
- [ ] Re-run focused tests, including close/reopen idempotency.
- [ ] Commit `feat: add fenced operator project controls`.

### Task 5: Bind operator generations to browser and local execution side effects

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Modify: `scorp-agent/master_a_dynamic_v4/browser_adapter.py`
- Modify: `scorp-agent/master_a_dynamic_v4/master_controller.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/v4_bridge_gateway.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_runtime_generation_fencing.py`

**Interfaces:**
- `StateStore.external_side_effect_gate(project_id, operator_generation, objective_generation) -> dict[str, Any]`.
- Intent payloads carry `operator_generation` and `objective_generation`.
- Browser submit and local execution call the gate immediately before external I/O.

- [ ] Write RED tests proving a failed durable generation write prevents browser `engine.submit` and `LocalExecutionAdapter.execute`, and proving cancel/supersede rejects old generation intents/results.
- [ ] Run the focused test and observe side-effect calls are currently not fenced.
- [ ] Add the gate and generation binding with fail-closed errors; preserve `MAY_HAVE_SUBMITTED` reconciliation behavior.
- [ ] Re-run focused tests and browser ambiguity regression tests.
- [ ] Commit `fix: fence external side effects by operator generation`.

### Task 6: Correct ActivationArbiter priority and daemon liveness timestamps

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/activation_arbiter.py`
- Modify: `scorp-agent/master_a_dynamic_v4/daemon.py`
- Modify: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Create or modify: `scorp-agent/master_a_dynamic_v4/tests/test_runtime_liveness_and_priority.py`

**Interfaces:**
- `ArbiterSnapshot` gains explicit operator state, auth/host blocker, pending result, and lost Worker fields with backward-compatible defaults.
- Health and observation records include `last_observed_at`, `last_progress_at`, `last_state_change_at`, `last_content_change_at`, `last_browser_success_at`, and `last_browser_error_at`.

- [ ] Write RED tests for stalled recovery outranking assignment, pause/cancel fence outranking recovery, explicit auth/host blockers, `IDLE` not advancing progress, and `ACTIVE_GENERATING` advancing progress.
- [ ] Run focused tests and confirm current priority/progress behavior fails at the expected assertions.
- [ ] Implement the fixed priority and persist observations; only content/generation progress updates `last_progress_at`.
- [ ] Re-run all arbiter and daemon tests.
- [ ] Commit `fix: fence stalled recovery and separate liveness from progress`.

### Task 7: Add the direct stdin/stdout CLI and package exports

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/runtime_cli.py`
- Modify: `scorp-agent/master_a_dynamic_v4/__init__.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_runtime_cli.py`
- Modify: `GPT_START_HERE.md` in the feature worktree with the local command instructions.

**Interfaces:**
- `python -m master_a_dynamic_v4.runtime_cli --database-path ... --project-id ... --daemon-epoch ...`.
- stdin: one JSON request per line; stdout: one JSON response per line; stderr: diagnostics only.

- [ ] Write RED tests for one valid status line, malformed JSON, unknown command, shell-shaped payload, and one mutation line producing a receipt.
- [ ] Run the focused CLI test and verify the entry point is absent or rejects the cases.
- [ ] Implement the bounded CLI with no subprocess, shell, browser, or URL imports; map parser/service errors to response status and exit code.
- [ ] Re-run focused CLI tests and manually pipe one status request through the installed Python runtime using a temporary SQLite database.
- [ ] Commit `feat: expose bounded runtime command stdio surface`.

### Task 8: Full regression, docs, and candidate verification

**Files:**
- Modify: `docs/superpowers/specs/2026-09-15-scorp-v4-fast-runtime-command-core-design.md` only for verified corrections.
- Modify: `docs/superpowers/plans/2026-09-15-scorp-v4-fast-runtime-command-core.md` to check completed steps.
- Create: `docs/handoffs/SCORP_V4_FAST_RUNTIME_COMMAND_CORE_VALIDATION.json`.

- [ ] Run all new focused tests and record exact counts.
- [ ] Run V4 core tests, the relevant GUI bridge tests, and `scripts/run-candidate-validation.ps1` with `SCORP_PYTHON` set to the bundled interpreter.
- [ ] Run compileall and `git diff --check`.
- [ ] Verify no scheduled task, browser profile, production SQLite, cookies, tokens, or production runtime changed.
- [ ] Record `TEST_VERIFIED`, `LIVE_VERIFIED`, and `ACCEPTED` separately; the command core’s offline PASS must not become live browser or production PASS.
- [ ] Commit `docs: record fast runtime command core verification`.

