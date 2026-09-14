# Persistent A + Dynamic Workers V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Scorp run one persistent Master identity A in one reusable ChatGPT conversation, dynamically dispatch up to three bounded ephemeral workers, fuse their evidence under A authority, and continue autonomously through D without user `继续` messages.

**Architecture:** D stays deterministic and local. A is the only reasoning/dispatch authority and normally reuses one canonical conversation across many turns. Workers are dynamic, bounded, event-driven, and ephemeral; their HANDOFF/BLOCKER output is reviewed by A before state mutation. Same-conversation continuation is the normal path; new A conversation is recovery-only.

**Tech Stack:** Python 3.14, asyncio, existing Scorp V3 durable relay/lease/session/GUI transport, Windows MCP GUI driver, unittest, Git/V4 control plane.

**Spec:** `docs/superpowers/specs/2026-09-12-persistent-a-dynamic-workers-v3-design.md`

## Global Constraints
- Exactly one persistent Master identity: `A`.
- `MAX_WORKERS=3` initially.
- Workers never dispatch other workers or publish production actions.
- Same A conversation is preferred for all normal continuations.
- New A conversation requires a recorded recovery reason.
- D never reasons about business/project semantics.
- PROJECT_STATE plus immutable root/acceptance hashes remain authoritative.
- At most one active A lease and one desktop GUI writer.
- TDD: every behavior change starts with a failing test and ends with the full Bridge suite green.

---

### Task 1: Reuse the canonical A conversation across lifecycle turns

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/continuation_watchdog_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/master_worker_coordinator_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/session_registry_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/parallel_master_worker_relay_v3.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_persistent_master_conversation_v3.py`

**Interfaces:**
- Consumes: authoritative project id/state version, current A lease, prior A session mapping.
- Produces: a new bound MASTER turn/session id whose GUI submission reuses the prior canonical A conversation URL when provenance is valid.

- [ ] **Step 1: Write failing tests** proving: first A resume opens a new chat; second normal resume has a distinct internal session/turn id but resolves the same canonical conversation URL; malformed/mismatched prior mapping fails closed; recovery rotation is explicit rather than silent.
- [ ] **Step 2: Run** `python -m unittest tests.test_persistent_master_conversation_v3 -v` and verify failures are specifically missing reuse/rotation behavior.
- [ ] **Step 3: Implement minimal conversation continuity** by separating persistent master conversation identity from bounded master session identity. Add session-registry helpers that resolve the latest proven A conversation for the same project and reject cross-project reuse. Pass that URL into transport submit for normal resume turns.
- [ ] **Step 4: Add explicit recovery rotation metadata** (`predecessor_conversation_url`, `rotation_reason`) only when reuse is impossible for a recognized transport/provenance reason. Never fall back silently.
- [ ] **Step 5: Run targeted and full suites:** `python -m unittest tests.test_persistent_master_conversation_v3 -v`; then `python -m unittest discover -s tests -p 'test_*.py'`; then `git diff --check`.
- [ ] **Step 6: Commit** `feat: reuse persistent master A conversation`.

### Task 2: Dynamic worker allocation with bounded parallelism

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/master_worker_relay_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/parallel_master_worker_relay_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/turn_scheduler_v3.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_dynamic_worker_allocation_v3.py`

**Interfaces:**
- Consumes: A `DISPATCH.assignments[]` with objective hash, task, optional resource scope, and success criteria.
- Produces: 0-3 dynamic `worker-*` identities, each with its own session/turn and bounded assignment; no fixed B/C semantics.

- [ ] **Step 1: Write failing tests** for zero-worker A work, one worker, three disjoint workers concurrently, a fourth assignment queued rather than submitted, duplicate objective rejection, worker no-dispatch authority, and conflicting resource scopes being serialized/rejected.
- [ ] **Step 2: Run** `python -m unittest tests.test_dynamic_worker_allocation_v3 -v` and verify expected RED behavior.
- [ ] **Step 3: Implement `MAX_WORKERS=3` scheduling** while retaining deterministic worker ids. Keep remote GPT generation concurrency but one GUI writer. Enforce objective uniqueness and resource-scope compatibility before concurrent submission.
- [ ] **Step 4: Ensure worker prompts bind** root/acceptance/objective hashes, exact task, response kinds HANDOFF/BLOCKER, and no-dispatch/no-production constraints.
- [ ] **Step 5: Run targeted/full tests and `git diff --check`**.
- [ ] **Step 6: Commit** `feat: add bounded dynamic worker scheduling`.

### Task 3: A review/fusion and drift guard

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/worker_event_queue_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/worker_event_pump_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/master_state_transition_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/gui_transport.py`
- Create: `scorp-agent/chatgpt-gui-bridge/worker_result_guard_v3.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_worker_result_guard_v3.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_master_worker_fusion_v3.py`

**Interfaces:**
- Consumes: durable worker HANDOFF/BLOCKER plus assignment binding and authoritative project state.
- Produces: validated worker event context for A; only A can commit fused semantic state.

- [ ] **Step 1: Write failing guard tests** for stale state version, wrong root/acceptance/objective hash, out-of-scope result metadata, duplicate identical replay, conflicting duplicate, and mismatched worker identity.
- [ ] **Step 2: Write failing fusion tests** proving worker completion does not mutate PROJECT_STATE by itself; A is reactivated with exact worker evidence; A may accept/reject/replan; only accepted A response mutates semantic state.
- [ ] **Step 3: Run both targeted modules** and verify RED.
- [ ] **Step 4: Implement deterministic `WorkerResultGuardV3`** returning structured verdicts such as ALIGNED, STALE, HASH_MISMATCH, SCOPE_VIOLATION, CONFLICT. The guard performs no reasoning.
- [ ] **Step 5: Enrich A event prompt** with assignment, guard verdict, evidence/test claims, current PROJECT_STATE, and explicit instruction that worker output is evidence rather than authority.
- [ ] **Step 6: Run targeted/full suites and `git diff --check`**.
- [ ] **Step 7: Commit** `feat: guard and fuse worker results through master A`.

### Task 4: Lifecycle closure for A and workers

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/master_window_lease_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/continuation_watchdog_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/durable_actor_transport_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/parallel_master_worker_relay_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/master_worker_coordinator_v3.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_d_lifecycle_preemption_v3.py`
- Test: `scorp-agent/chatgpt-gui-bridge/tests/test_actor_timeout_liveness_v3.py`

**Interfaces:**
- Consumes: A lease deadlines, worker submission timestamps, durable transport state.
- Produces: planned same-conversation A handoff, hard A recovery, deterministic worker timeout BLOCKER, and no permanently inflight actor.

- [ ] **Step 1: Bring the existing lifecycle RED tests into the isolated worktree** and add explicit same-conversation soft handoff expectations.
- [ ] **Step 2: Bring/repair actor-timeout RED tests** proving SUBMITTED -> TIMED_OUT is terminal/idempotent; timed-out worker becomes deterministic `ACTOR_GUI_TIMEOUT` BLOCKER; timed-out master is terminalized operationally so D may resume; young submissions remain pending.
- [ ] **Step 3: Run targeted tests and verify RED**.
- [ ] **Step 4: Implement fixed soft/hard A deadlines**; heartbeat may not extend hard deadline; DRAIN at/after planned handoff reactivates same A conversation; hard expiration releases ownership and resumes from durable state.
- [ ] **Step 5: Implement actor timeout terminalization** without unsafe HWND closure and without fabricating a semantic Master response.
- [ ] **Step 6: Run lifecycle/timeout targeted tests, full 270+ suite, compileall, and `git diff --check`**.
- [ ] **Step 7: Commit** `feat: close master and worker lifecycle supervision`.

### Task 5: Unattended production E2E and deployment

**Files:**
- Create: `scorp-agent/chatgpt-gui-bridge/tools/persistent_a_dynamic_workers_e2e_v3.py`
- Modify as needed: `scorp-agent/chatgpt-gui-bridge/install-bridge.ps1`
- Test: existing production/runtime hardening suites.

**Interfaces:**
- Consumes: installed production Bridge, one ACTIVE project contract, Git-only control plane.
- Produces: durable proof of autonomous A/worker/D loop with no follow-up command from the initiating chat.

- [ ] **Step 1: Write an isolated installed-runtime canary** whose root objective requires A to dispatch at least two disjoint workers, WAIT, receive HANDOFFs, fuse them, D reactivate the same canonical A conversation for a later turn, and TERMINAL COMPLETE.
- [ ] **Step 2: Acceptance assertions:** exactly one persistent A identity; same canonical A conversation across at least three A turns; distinct internal turn/session ids; at least two worker sessions; all worker events ACKED; no stale inflight rows; no AMBIGUOUS transport; no second active master lease; project terminal/archive valid.
- [ ] **Step 3: Run the canary once with a single initiating control action. After start, issue no control/write commands until terminal or timeout; read-only observation is allowed.**
- [ ] **Step 4: If GREEN, run full Bridge regression, V4 critical/production hardening, and static installation checks.**
- [ ] **Step 5: Fast-forward the verified feature commit to `scorp-master-worker-v3`, deploy installed runtime transactionally, and rerun the unattended canary.**
- [ ] **Step 6: Commit/release evidence** with exact source SHA, installed-file SHA, test counts, A conversation URL continuity proof, worker event counts, and terminal/archive proof.

## Completion Gate
The project is complete only when Task 5 passes on the installed production path without any user `继续` and without the initiating chat sending another control/write command after the canary starts. A normal continuation must reuse the same A conversation; conversation rotation is recovery-only and must be recorded.
