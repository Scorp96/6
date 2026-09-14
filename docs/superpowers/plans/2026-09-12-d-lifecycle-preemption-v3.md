# D Lifecycle Preemption V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deterministic D manage each Master A window from birth through a fixed soft handoff and hard takeover deadline so A continues without user `继续` messages.

**Architecture:** Extend the durable master lease with additive fixed lifecycle deadlines, cap lease renewal at the hard deadline, and make ContinuationWatchdog reason only from authoritative project/lease state and time. Bind the lifecycle schedule into every Master turn so A checkpoints and returns DRAIN before the soft deadline; D alone creates the successor after revalidation.

**Tech Stack:** Python 3, `unittest`, JSON durable state, existing Scorp Master/Worker V3 bridge/runtime, Windows production GUI transport.

**Spec:** `docs/superpowers/specs/2026-09-12-d-lifecycle-preemption-v3-design.md`

## Global Constraints

- GPT-5.6 Sol remains the only reasoning controller; D remains deterministic and non-reasoning.
- Do not invoke Codex or any local model.
- Git-only control path; RDC is not part of this architecture.
- PROJECT_STATE and immutable project contract remain semantic authority; D must not mutate PROJECT_STATE.
- Persistent Master identity is exactly `A`; individual A windows are disposable sessions.
- Default hard Master window lifetime remains `1500` seconds.
- Default soft handoff lead is `60` seconds.
- Existing handoff reserve remains `300` seconds for checkpoint preparation.
- Never queue a successor while the predecessor still owns a live ACTIVE lease before hard deadline.
- Terminal project states `COMPLETE` and `HARD_BLOCKED` never resume.
- Preserve stale-HWND provenance fail-closed behavior; lifecycle takeover never authorizes closing an unverified GUI window.
- Use TDD: every behavior change must have a failing test observed before production code is changed.

---

### Task 1: Fixed Master Lifecycle Lease

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/master_window_lease_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/tests/test_master_window_lease_v3.py`

**Interfaces:**
- Consumes: existing `MasterWindowLeaseStore.acquire()`, `heartbeat()`, `is_active()`, `drain()`, `release()`.
- Produces: additive lease keys `soft_handoff_at`, `hard_handoff_at`, `hard_lifetime_seconds`, `soft_handoff_lead_seconds`; helper accessors for lifecycle-deadline evaluation if needed by D.

- [ ] **Step 1: Write failing lease lifecycle tests**

Add tests that assert:

```python
lease = store.acquire(
    "p", "a-window-7", "turn-7", 7,
    now=now,
    ttl_seconds=1500,
    soft_handoff_lead_seconds=60,
)
assert lease["soft_handoff_at"] == "2026-09-11T09:24:00Z"
assert lease["hard_handoff_at"] == "2026-09-11T09:25:00Z"
```

Also assert a heartbeat near the end cannot move `lease_until` or `hard_handoff_at` past the original hard deadline; `is_active()` is false at the hard deadline; invalid soft lead (`<=0` or `>= ttl`) fails closed; a legacy lease with only `lease_until` remains readable and derives its hard boundary without rewriting the file.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -m unittest tests.test_master_window_lease_v3 -v
```

Expected: new lifecycle assertions fail because current acquire/heartbeat do not persist/cap fixed deadlines.

- [ ] **Step 3: Implement the minimal fixed-lifecycle lease behavior**

Implement additive deadline fields at acquisition and cap heartbeat renewal:

```python
hard_at = now + dt.timedelta(seconds=ttl)
soft_at = hard_at - dt.timedelta(seconds=soft_lead)
new_lease_until = min(now + dt.timedelta(seconds=ttl), hard_at)
```

Use strict timezone-aware parsing. Keep old lease files readable and do not rewrite them during read-only evaluation.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same unittest command. Expected: all lease tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scorp-agent/chatgpt-gui-bridge/master_window_lease_v3.py scorp-agent/chatgpt-gui-bridge/tests/test_master_window_lease_v3.py
git commit -m "feat: add fixed master lifecycle deadlines"
```

### Task 2: Deterministic Planned Handoff in D

**Files:**
- Modify: `scorp-agent/chatgpt-gui-bridge/continuation_watchdog_v3.py`
- Modify: `scorp-agent/chatgpt-gui-bridge/tests/test_continuation_watchdog_v3.py`

**Interfaces:**
- Consumes: lifecycle fields from `MasterWindowLeaseStore` and authoritative PROJECT_STATE.
- Produces: deterministic D statuses `MASTER_ACTIVE`, `HANDOFF_DUE`, `WAITING_HANDOFF_WINDOW`, `PLANNED_HANDOFF`, `HARD_DEADLINE_EXPIRED`, plus existing resume statuses.

- [ ] **Step 1: Write failing D lifecycle tests**

Create focused cases:

```python
# ACTIVE before soft -> no successor
assert watchdog.run_once(now=soft - dt.timedelta(seconds=1))["status"] == "MASTER_ACTIVE"

# ACTIVE inside soft window -> still no successor / no split brain
assert watchdog.run_once(now=soft)["status"] == "HANDOFF_DUE"
assert not list(outbox.glob("*.json"))

# DRAINED before soft -> wait
assert watchdog.run_once(now=soft - dt.timedelta(seconds=10))["status"] == "WAITING_HANDOFF_WINDOW"

# same DRAINED lease at soft -> successor
assert watchdog.run_once(now=soft)["reason"] == "PLANNED_HANDOFF"

# ACTIVE at hard -> hard takeover
assert watchdog.run_once(now=hard)["reason"] == "HARD_DEADLINE_EXPIRED"
```

Assert terminal project state always suppresses every timer path. Assert repeated ticks create one identical RESUME_MASTER file.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -m unittest tests.test_continuation_watchdog_v3 -v
```

Expected: lifecycle-specific status/reason assertions fail on current post-expiry-only D behavior.

- [ ] **Step 3: Implement D decision table**

Keep `_resume_turn()` deterministic. Include predecessor `soft_handoff_at` and `hard_handoff_at` in identity material and lifecycle payload. Do not mutate PROJECT_STATE. Do not queue a successor for ACTIVE predecessor between soft and hard.

- [ ] **Step 4: Run focused tests and verify GREEN**

Expected: all continuation watchdog tests PASS, including existing contract-context behavior.

- [ ] **Step 5: Commit**

```bash
git add scorp-agent/chatgpt-gui-bridge/continuation_watchdog_v3.py scorp-agent/chatgpt-gui-bridge/tests/test_continuation_watchdog_v3.py
git commit -m "feat: add deterministic planned master handoff"
```

### Task 3: Bind Lifecycle Schedule Into A

**Files:**
- Modify: the existing V3 MASTER prompt renderer discovered in the bridge source
- Modify: its focused prompt tests (prefer existing `tests/test_actor_prompt_v3.py` or the exact V3 prompt test module)
- Modify only if required: `scorp-agent/chatgpt-gui-bridge/master_state_transition_v3.py`

**Interfaces:**
- Consumes: MASTER turn payload `master_lifecycle` from D/lease acquisition.
- Produces: explicit A instruction to checkpoint and DRAIN by soft deadline; existing response kind `DRAIN` remains the protocol operation.

- [ ] **Step 1: Locate and read the exact V3 MASTER prompt path**

Do not patch the older V1 role relay prompt. Trace the current production `scorp.master-worker/turn-v3` rendering path and its tests.

- [ ] **Step 2: Write failing prompt tests**

Assert a MASTER prompt containing lifecycle metadata includes the exact soft/hard timestamps and unambiguous rules:

```text
checkpoint PROJECT_STATE before handoff
return DRAIN no later than SOFT_HANDOFF_AT
D does successor creation; A must not start another A
```

Assert worker prompts do not receive Master-only drain instructions.

- [ ] **Step 3: Run focused tests and verify RED**

Expected: current prompt does not contain the lifecycle contract.

- [ ] **Step 4: Implement minimal prompt/payload wiring**

Do not create a new response kind. Reuse `DRAIN`; preserve current transition durability and replay behavior. If transition code needs lifecycle validation, make only the smallest binding change required by the tests.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run prompt tests plus:

```powershell
C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -m unittest tests.test_master_state_transition_v3 tests.test_master_context_continuity_v3 -v
```

- [ ] **Step 6: Commit**

Commit prompt/wiring and tests with message:

```bash
git commit -m "feat: bind master lifecycle handoff contract"
```

### Task 4: Production Runtime Integration and Regression

**Files:**
- Verify/modify only if required: `scorp-agent/chatgpt-gui-bridge/production_v3_runtime.py`
- Verify/modify installer manifest only if new runtime files are introduced
- Tests: production/runtime/contract/integration suites

**Interfaces:**
- Consumes: fixed lease store + D behavior + lifecycle-bound Master prompt.
- Produces: unchanged production daemon entry point with proactive lifecycle behavior enabled by default.

- [ ] **Step 1: Add production integration test if current construction does not prove lifecycle defaults**

Assert ProductionV3Runtime constructs the same lease/watchdog path with 1500-second hard lifetime and 60-second soft lead.

- [ ] **Step 2: Verify RED only if a production wiring gap exists**

If existing construction automatically consumes the modified components, document that no production source change is needed instead of inventing one.

- [ ] **Step 3: Run full Bridge regression**

```powershell
C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe -m compileall .
git diff --check
```

Expected: full suite green, compileall green, clean diff check.

- [ ] **Step 4: Commit any necessary integration/test-only changes**

Use a focused commit; do not mix unrelated watchdog/installer fixes.

### Task 5: Deploy and Prove Autonomous Handoff

**Files:**
- No new design files.
- Use existing transactional bridge installer/deployment path.
- Add a dedicated test tool only if needed for deterministic short-lifecycle E2E.

**Interfaces:**
- Consumes: fully green feature branch.
- Produces: production evidence for planned handoff and hard takeover.

- [ ] **Step 1: Merge/fast-forward feature only after regression evidence is green**

Use CAS against the expected `scorp-master-worker-v3` head. Abort on head drift.

- [ ] **Step 2: Transactionally deploy production bridge**

Preserve current project state and verify installed SHA, single bridge root, watchdog health, and `health.error=null`.

- [ ] **Step 3: Run planned-handoff E2E with a short isolated lifecycle**

Use a test-only lifecycle override so validation takes minutes, not 25 minutes. Required observable sequence:

```text
A1 acquires fixed lifecycle
A1 checkpoints and DRAINs
no user message
D waits until soft boundary
D queues A2 exactly once
A2 receives the same project contract/state and continues
```

Verify no overlapping live Master lease exists.

- [ ] **Step 4: Run hard-takeover E2E**

Deliberately let A1 fail to DRAIN. Without user input, at hard deadline D must queue A2 exactly once. Preserve stale-HWND fail-closed behavior.

- [ ] **Step 5: Final production gate**

Only declare autonomous continuation complete after both E2E paths pass and a reboot/recovery check confirms the same behavior survives process restart.
