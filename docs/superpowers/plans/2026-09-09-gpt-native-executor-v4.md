# GPT-Native Scorp Executor V4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a Windows executor that performs GPT-authored deterministic actions with crash-safe exactly-once semantics and zero Codex dependency.

**Architecture:** GPT-5.6 Sol owns all reasoning and emits versioned JSON action envelopes through GitHub issues. A PowerShell executor persists state, verifies claim/result identity through authoritative GitHub REST reads, launches a gated deterministic runner, and returns structured evidence. A one-time bootstrap atomically migrates the existing interactive-user Scheduled Task after local tests pass.

**Tech Stack:** Windows PowerShell 5.1, GitHub CLI (`gh`), Git, Task Scheduler, JSON, Windows process/CIM APIs.

**Spec:** `docs/superpowers/specs/2026-09-09-gpt-native-executor-v4-design.md`

## Global Constraints

- GPT-5.6 Sol is the only intelligence layer; V4 must not invoke Codex.
- Production candidate branch is `scorp-gpt-native-v4`; do not modify `main`.
- Preserve interactive-user Scheduled Task execution; do not switch to SYSTEM.
- Default executor poll interval is 5 seconds; per-action timeout is 1..1800 seconds.
- All GitHub comment body files and durable JSON state are UTF-8 without BOM.
- Ambiguous action/result outcomes fail closed; no blind rerun or blind repost.

---

### Task 1: Close the pre-claim crash window

**Files:**
- Modify: `scorp-agent/executor-v4.1.ps1`
- Test: built-in `Invoke-SelfTest`

**Interfaces:**
- Consumes: queued `[SCORP_EXEC]` issue and validated envelope.
- Produces: durable `PREPARED` state before any queue title/claim mutation.

- [ ] **Step 1: Add a failing self-test for recovery-safe title transition**

Add pure helpers `Get-ExpectedRunningTitle` and `Get-RunningTransitionDecision` and assert:

```powershell
Assert-Test ((Get-RunningTransitionDecision '[SCORP_EXEC] A' '[SCORP_EXEC] A' '[SCORP_EXEC_RUNNING] A') -ceq 'TRANSITION') 'running-title-transition'
Assert-Test ((Get-RunningTransitionDecision '[SCORP_EXEC_RUNNING] A' '[SCORP_EXEC] A' '[SCORP_EXEC_RUNNING] A') -ceq 'ALREADY') 'running-title-recovery'
```

- [ ] **Step 2: Run Windows PowerShell self-test**

Run:

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\scorp-agent\executor-v4.1.ps1 -SelfTest -MutexName "Local\ScorpV4PlanTitleTest"
```

Expected before implementation: FAIL because the helpers are not defined.

- [ ] **Step 3: Implement durable ordering**

`Prepare-QueuedIssue` must write envelope + durable `PREPARED` state before changing the issue title. `Resume-State` must call an idempotent `Ensure-RunningTitle` before claim publication. A restart with the title already running must continue; any unrelated title fails closed.

- [ ] **Step 4: Re-run self-test**

Expected: new tests and existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scorp-agent/executor-v4.1.ps1
git commit -m "fix: persist V4 state before queue transition"
```

### Task 2: Prevent prepared work from being rejected after transient recovery errors

**Files:**
- Modify: `scorp-agent/executor-v4.1.ps1`

**Interfaces:**
- Produces: rejection only for validation/preparation failures that occur before durable state exists.

- [ ] **Step 1: Add failing policy self-test**

Create pure helper:

```powershell
function Get-PrepareFailureDisposition {
    param([bool]$StateExists)
    if ($StateExists) { return 'RECOVER' }
    return 'REJECT'
}
```

Assert both outcomes.

- [ ] **Step 2: Run self-test and confirm RED**

Expected: FAIL before helper exists.

- [ ] **Step 3: Change main-loop error handling**

After `Prepare-QueuedIssue` throws, check `Read-State`. If state exists, log and let the next loop recover it. Call `Reject-QueuedIssue` only when no active state was durably created.

- [ ] **Step 4: Re-run self-test**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scorp-agent/executor-v4.1.ps1
git commit -m "fix: recover prepared V4 work after transient errors"
```

### Task 3: Add cross-issue action-id idempotency ledger

**Files:**
- Modify: `scorp-agent/executor-v4.1.ps1`
- Modify: `scorp-agent/runner-v4.1.ps1` only if result metadata needs extension.

**Interfaces:**
- Produces: `C:\ScorpAgent\state-v4\actions\<safe-action-id>.json`.

- [ ] **Step 1: Add failing ledger decision tests**

Test pure decision cases:

```powershell
Assert-Test ((Get-LedgerDecision $null 'abc') -ceq 'CREATE') 'ledger-new'
Assert-Test ((Get-LedgerDecision ([pscustomobject]@{ envelope_sha256='abc'; terminal=$true }) 'abc') -ceq 'ALREADY_DONE') 'ledger-terminal-same'
Assert-Test ((Get-LedgerDecision ([pscustomobject]@{ envelope_sha256='def'; terminal=$false }) 'abc') -ceq 'CONFLICT') 'ledger-hash-conflict'
```

- [ ] **Step 2: Run self-test and confirm RED**

- [ ] **Step 3: Implement canonical envelope hash and ledger writes**

Hash the exact persisted execution envelope bytes. Before preparing a new action, consult the action ledger. A terminal same-hash action must never run again; a different hash for the same action_id is blocked as a conflict. Update ledger phase alongside active-state transitions and final result hash.

- [ ] **Step 4: Re-run self-test**

- [ ] **Step 5: Commit**

```bash
git add scorp-agent/executor-v4.1.ps1 scorp-agent/runner-v4.1.ps1
git commit -m "feat: add V4 action idempotency ledger"
```

### Task 4: Expand deterministic recovery self-tests

**Files:**
- Modify: `scorp-agent/executor-v4.1.ps1`

**Interfaces:**
- Tests pure recovery decisions without GitHub or GUI side effects.

- [ ] **Step 1: Add tests for gated-child decision states**

Cover:
- durable PREPARED before title mutation;
- CLAIM_VERIFIED with discovered gated child -> adopt;
- RUNNING with durable result -> finalize;
- RUNNING with gate present/no child/no result -> ambiguous block;
- duplicate matching claim/result -> block.

- [ ] **Step 2: Add atomic replace regression**

Write the same state file twice with `Write-JsonAtomic`, read it back, and verify the second value. This specifically guards Windows `File.Replace` behavior.

- [ ] **Step 3: Run full self-test**

Expected: every named test prints `PASS` and final `SELFTEST PASS`.

- [ ] **Step 4: Commit**

```bash
git add scorp-agent/executor-v4.1.ps1
git commit -m "test: expand V4 recovery regressions"
```

### Task 5: Pin bootstrap installation to one immutable branch snapshot and add rollback

**Files:**
- Modify: `scorp-agent/bootstrap-v4.ps1`

**Interfaces:**
- Consumes: mutable branch name once.
- Produces: one resolved commit SHA used for all downloaded files and rollback evidence.

- [ ] **Step 1: Resolve branch SHA once**

Use:

```powershell
$resolvedSha = (& gh api "/repos/$Repo/commits/$Ref" --jq '.sha').Trim()
if ($LASTEXITCODE -ne 0 -or $resolvedSha -notmatch '^[0-9a-f]{40}$') { throw 'cannot resolve immutable V4 commit' }
```

Then fetch all content with `?ref=$resolvedSha`.

- [ ] **Step 2: Verify executor/runner syntax and self-test before Task Scheduler mutation**

Expected: any failure exits before `Stop-ScheduledTask`.

- [ ] **Step 3: Add post-switch health verification**

After starting the Scheduled Task, verify:
- task action points to installed `executor-v4.1.ps1`;
- task is not immediately terminal/disabled;
- one matching PowerShell process can be found for the installed executor;
- no new Codex process is attributable to the switch.

- [ ] **Step 4: Add rollback**

If post-switch verification fails, restore the prior action captured before mutation, restart the old task, record rollback evidence, then throw.

- [ ] **Step 5: Commit**

```bash
git add scorp-agent/bootstrap-v4.ps1
git commit -m "fix: pin and rollback V4 bootstrap"
```

### Task 6: Static no-Codex and branch review gate

**Files:**
- Review: `scorp-agent/executor-v4.1.ps1`
- Review: `scorp-agent/runner-v4.1.ps1`
- Review: `scorp-agent/bootstrap-v4.ps1`
- Review: `scorp-agent/executor-v4.schema.json`

- [ ] **Step 1: Search V4 install/runtime files for Codex invocation**

Run:

```powershell
Select-String -Path .\scorp-agent\executor-v4.1.ps1,.\scorp-agent\runner-v4.1.ps1,.\scorp-agent\bootstrap-v4.ps1 -Pattern 'codex\s+exec|codex\.exe' -CaseSensitive:$false
```

Expected: no invocation matches. A health metric mentioning a Codex process name is allowed only as observation and must not launch it.

- [ ] **Step 2: Run parser checks, self-test, and `git diff --check`**

Expected: all PASS / no output from `git diff --check`.

- [ ] **Step 3: Record final branch SHA in issue #16**

Do not mark GREEN yet; Windows acceptance is still required.

### Task 7: One-time Windows bootstrap and acceptance

**Files:**
- Execute: `scorp-agent/bootstrap-v4.ps1`
- Evidence: `C:\ScorpAgent\state-v4\bootstrap-v4-evidence.json`

- [ ] **Step 1: Run the single bootstrap command on SCORP**

Use the immutable branch implementation; bootstrap itself resolves and records the exact install SHA.

- [ ] **Step 2: Verify task/executor health**

Confirm one executor PID, correct Scheduled Task action, mutex second-instance denial, and no Codex launch.

- [ ] **Step 3: Submit three GPT-authored deterministic test actions**

Use unique action IDs for:
1. `health`;
2. `file_write` to a disposable file under `C:\ScorpAgent\state-v4\acceptance`;
3. `file_read` of that same file with expected SHA evidence.

Expected for every issue: one claim, one identity-bound result, one close.

- [ ] **Step 4: Restart recovery test**

Run a bounded deterministic sleep/process action, restart the executor while the gated/recorded action is active, and verify no second execution/result.

- [ ] **Step 5: Duplicate action-id test**

Submit a second issue with a previously completed action_id. Expected: no local rerun; executor blocks/links prior terminal ledger evidence.

- [ ] **Step 6: Quiet-window and mutex checks**

Wait >=30 seconds after final result and verify no extra result. Start a second executor instance with the production mutex and verify `MUTEX_DENIED`.

- [ ] **Step 7: Close #16 GREEN only after all evidence is fresh**

Then activate #14 Full Auto Orchestrator.