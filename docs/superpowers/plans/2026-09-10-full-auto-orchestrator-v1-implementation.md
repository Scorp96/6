# Full Auto Orchestrator V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Windows-side orchestrator that schedules GPT-authored exact actions through accepted Scorp Executor V4.1, persists registry/WAL/continuation state, survives restart without duplicate execution, and requests unattended GPT-5.6 Sol continuation through a non-executable GitHub control protocol.

**Architecture:** `GPT-5.6 Sol -> durable task/continuation records -> Local Orchestrator V1 -> Scorp Executor V4.1 -> deterministic result -> Local Orchestrator -> GPT-5.6 Sol continuation when reasoning is required`. The orchestrator never invents commands or next steps. Local registry snapshot + append-only WAL are authoritative for scheduling state; GitHub is cloud mirror/control and V4 remains authoritative for action execution exactly-once semantics.

**Tech Stack:** Windows PowerShell 5.1, JSON/JSONL, GitHub CLI/REST, Windows Task Scheduler, WMI/CIM, existing Scorp Executor V4.1 protocol `scorp.exec/v4`.

**Spec:** `docs/superpowers/specs/2026-09-10-full-auto-orchestrator-v1-design.md`

## Global Constraints

- Development branch: `scorp-full-auto-orchestrator-v1`; do not modify `main` or `scorp-gpt-native-v4`.
- Accepted execution plane: V4.1 release `80e41e183441a8498d5ba943cdc8b976b8fbb3a8`.
- GPT-5.6 Sol is the only reasoning/planning/coding/debugging/continuation intelligence layer.
- No Codex CLI, `codex exec`, Codex fallback, or local reasoning model.
- Local orchestrator schedules only complete GPT-authored exact `next_action` records.
- Missing next reasoning step becomes `WAITING` + `GPT_CONTINUATION_REQUIRED`; local code never manufactures a continuation.
- Local runtime root: `C:\ScorpAgent\orchestrator-v1\`.
- Scheduled Task name: `ScorpFullAutoOrchestrator`; an incompatible pre-existing task with that name is a deployment failure.
- Registry mutations use single-writer named-mutex serialization, append-only WAL first, then atomic snapshot replacement.
- V4 owns child identity, timeout safety, action ledger, claim/result exactly-once, and executor mutex.
- Any ambiguous publication/result/recovery outcome fails closed; never duplicate a V4 action because a network response was lost.
- `#15` is metadata registry only and must never be treated as a `[SCORP_EXEC]` envelope.
- Approval-gated operations remain approval-gated: external sends, payments/transfers, account-security changes, destructive deletion, irreversible production changes.
- Local READY pickup acceptance target: <=5 minutes when an exact next action already exists.
- Unattended new-reasoning latency is separate; full GREEN requires a verified unattended GPT-5.6 Sol host bridge.
- Every implementation task follows TDD: failing regression -> observed RED -> minimal implementation -> observed GREEN -> commit.

---

## File Structure

- `scorp-agent/orchestrator-v1.ps1` — process entrypoint, named mutex, deterministic scheduler loop, module composition only.
- `scorp-agent/orchestrator/task-schema-v1.json` — task record JSON Schema.
- `scorp-agent/orchestrator/continuation-schema-v1.json` — continuation request/decision JSON Schema.
- `scorp-agent/orchestrator/store-v1.ps1` — UTF-8 no-BOM IO, canonical hashing, WAL append/flush, atomic snapshot, replay/recovery.
- `scorp-agent/orchestrator/registry-v1.ps1` — transition validation, task mutation, runnable selection, generation/no-spin rules.
- `scorp-agent/orchestrator/health-control-v1.ps1` — control/health schema, atomic health writes, pause/emergency behavior.
- `scorp-agent/orchestrator/router-v4.ps1` — V4 issue publication and authoritative claim/result reconciliation.
- `scorp-agent/orchestrator/continuation-v1.ps1` — continuation outbox, `[SCORP_CONT_REQ]`, trusted decision validation/application, idempotent cleanup.
- `scorp-agent/orchestrator/bootstrap-orchestrator-v1.ps1` — commit-pinned install + separate Scheduled Task deployment/rollback.
- `scorp-agent/orchestrator/release-manifest-orchestrator-v1.json` — immutable artifact blob map used by bootstrap.
- `scorp-agent/tests/orchestrator-v1-contract-store.ps1` — contract/store/WAL regression suite.
- `scorp-agent/tests/orchestrator-v1-scheduler-control.ps1` — scheduler/mutex/pause/no-spin regression suite.
- `scorp-agent/tests/orchestrator-v1-router-continuation.ps1` — V4 router and continuation binding regression suite.
- `scorp-agent/tests/orchestrator-v1-recovery-deployment.ps1` — restart/deployment/rollback regression suite.
- `scorp-agent/tests/orchestrator-v1-acceptance.ps1` — static acceptance gates and helper fixtures.

---

### Task 1: Task and Continuation Contracts + Durable Store

**Files:**
- Create: `scorp-agent/orchestrator/task-schema-v1.json`
- Create: `scorp-agent/orchestrator/continuation-schema-v1.json`
- Create: `scorp-agent/orchestrator/store-v1.ps1`
- Create: `scorp-agent/tests/orchestrator-v1-contract-store.ps1`

**Interfaces:**
- Produces: `Write-Utf8NoBomAtomic($Path,$Text)`, `Get-Sha256Text($Text)`, `Append-WalRecord($Root,$Record)`, `Read-RegistrySnapshot($Root)`, `Write-RegistrySnapshot($Root,$Registry)`, `Recover-RegistryFromWal($Root)`, `Assert-TaskRecord($Task)`, `Assert-ContinuationRequest($Request)`, `Assert-ContinuationDecision($Decision)`.
- Consumes: Windows PowerShell 5.1 only; no external module dependency.

- [ ] **Step 1: Write failing contract/store tests**

Create a test fixture that asserts:

```powershell
$taskSchema = Get-Content $TaskSchemaPath -Raw | ConvertFrom-Json
Assert-True ($taskSchema.properties.task_id.type -eq 'string') 'task-id-string'
Assert-True (@($taskSchema.properties.state.enum) -contains 'READY') 'task-state-ready'
Assert-True ($taskSchema.properties.next_action) 'task-next-action-defined'
Assert-True ($taskSchema.properties.lease) 'task-lease-defined'

$decisionSchema = Get-Content $ContinuationSchemaPath -Raw | ConvertFrom-Json
Assert-True ($decisionSchema.definitions.decision.properties.decision_id.type -eq 'string') 'decision-id-string'
Assert-True ($decisionSchema.definitions.decision.required -contains 'expected_registry_sequence') 'decision-generation-bound'

. $StorePath
Write-Utf8NoBomAtomic -Path $tmp -Text '{"x":1}'
$bytes=[IO.File]::ReadAllBytes($tmp)
Assert-True (-not ($bytes.Length-ge3 -and $bytes[0]-eq0xEF -and $bytes[1]-eq0xBB -and $bytes[2]-eq0xBF)) 'utf8-no-bom'
```

Add WAL recovery cases:

```powershell
$root=Join-Path $env:TEMP ('scorp-orch-store-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root|Out-Null
$reg=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@()}
Write-RegistrySnapshot $root $reg
Append-WalRecord $root ([ordered]@{sequence=1;prior_sequence=0;mutation='TEST';registry=$([ordered]@{protocol_version='scorp.orchestrator/v1';sequence=1;tasks=@()})})
$recovered=Recover-RegistryFromWal $root
Assert-True ($recovered.sequence -eq 1) 'wal-replay-one-record'
```

- [ ] **Step 2: Run tests and verify RED**

Run on Windows PowerShell 5.1 through accepted V4:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scorp-agent\tests\orchestrator-v1-contract-store.ps1
```

Expected: FAIL because schemas/store functions do not yet exist.

- [ ] **Step 3: Implement minimal schemas and store primitives**

`task-schema-v1.json` must require at least:

```json
{
  "protocol_version":"scorp.orchestrator/task-v1",
  "task_id":"example",
  "project_id":"example",
  "priority":100,
  "state":"READY",
  "dependencies":[],
  "dependency_policy":"all_done",
  "parallel_safety":false,
  "execution_adapter":"v4",
  "next_action":null,
  "continuation_request_id":null,
  "checkpoint":null,
  "attempts":0,
  "max_attempts":1,
  "next_eligible_at":null,
  "created_at":"2026-09-10T00:00:00Z",
  "updated_at":"2026-09-10T00:00:00Z",
  "last_result":null,
  "evidence_refs":[],
  "safety_class":"standard",
  "authorization_requirement":null,
  "waiting_reason":null,
  "blocker":null,
  "lease":null,
  "generation":0,
  "resource_scope":[]
}
```

`store-v1.ps1` implementation rules:

```powershell
function Write-Utf8NoBomAtomic {
    param([string]$Path,[string]$Text)
    $dir=Split-Path -Parent $Path
    if(-not(Test-Path $dir)){New-Item -ItemType Directory -Path $dir -Force|Out-Null}
    $tmp="$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    [IO.File]::WriteAllText($tmp,$Text,(New-Object Text.UTF8Encoding($false)))
    if(Test-Path $Path){[IO.File]::Replace($tmp,$Path,$null)}else{Move-Item -LiteralPath $tmp -Destination $Path}
}

function Append-WalRecord {
    param([string]$Root,$Record)
    $line=($Record|ConvertTo-Json -Depth 64 -Compress)+[Environment]::NewLine
    $path=Join-Path $Root 'journal.jsonl'
    $bytes=(New-Object Text.UTF8Encoding($false)).GetBytes($line)
    $fs=[IO.File]::Open($path,[IO.FileMode]::Append,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{$fs.Write($bytes,0,$bytes.Length);$fs.Flush($true)}finally{$fs.Dispose()}
}
```

`Recover-RegistryFromWal` must reject non-monotonic `sequence`, mismatched `prior_sequence`, or incompatible snapshot protocol rather than guess.

- [ ] **Step 4: Run tests and verify GREEN**

Expected final markers:

```text
PASS utf8-no-bom
PASS atomic-replace
PASS wal-replay-one-record
PASS wal-fork-fails-closed
PASS task-schema-required-fields
PASS continuation-decision-generation-bound
ORCHESTRATOR_V1_CONTRACT_STORE_PASS
```

- [ ] **Step 5: Commit**

```text
feat: add orchestrator v1 contracts and durable store
```

---

### Task 2: Registry State Machine, Deterministic Scheduler, Pause/Health

**Files:**
- Create: `scorp-agent/orchestrator/registry-v1.ps1`
- Create: `scorp-agent/orchestrator/health-control-v1.ps1`
- Create: `scorp-agent/orchestrator-v1.ps1`
- Create: `scorp-agent/tests/orchestrator-v1-scheduler-control.ps1`

**Interfaces:**
- Consumes: Task 1 store functions.
- Produces: `Invoke-RegistryMutation($Root,$TaskId,$Mutation)`, `Get-RunnableTasks($Registry,$Now)`, `Select-NextTask($Registry,$Now)`, `Get-ControlState($Root)`, `Set-HealthState($Root,$Health)`, `Try-AcquireOrchestratorMutex()`, `Invoke-OrchestratorTick($Context)`.

- [ ] **Step 1: Write failing scheduler/control tests**

Tests must cover deterministic ordering and no local reasoning:

```powershell
$tasks=@(
  [pscustomobject]@{task_id='b';priority=100;state='READY';next_eligible_at=$null;created_at='2026-09-10T00:00:00Z';dependencies=@();resource_scope=@();next_action=@{action_id='b1'}},
  [pscustomobject]@{task_id='a';priority=100;state='READY';next_eligible_at=$null;created_at='2026-09-10T00:00:00Z';dependencies=@();resource_scope=@();next_action=@{action_id='a1'}}
)
$selected=Select-NextTask ([pscustomobject]@{tasks=$tasks}) ([DateTimeOffset]::Parse('2026-09-10T01:00:00Z'))
Assert-True ($selected.task_id -eq 'a') 'stable-task-id-tiebreak'
```

Tests must also assert:

```powershell
# READY without next_action must not run.
Assert-True ((Get-RunnableTasks $reg $now).task_id -notcontains 'missing-action') 'no-local-reasoning'
# paused prevents launch but does not clear RUNNING lease.
# unchanged FAILED/WAITING generation does not spin/reprocess.
# dependency not DONE prevents READY pickup.
# overlapping resource_scope prevents conflicting selection.
# second mutex probe returns MUTEX_DENIED.
```

- [ ] **Step 2: Run tests and verify RED**

Expected: FAIL because registry/scheduler/control functions do not exist.

- [ ] **Step 3: Implement minimal deterministic scheduler**

`Get-RunnableTasks` must be pure filtering; it never writes state. `Select-NextTask` sorts by:

```powershell
$eligible | Sort-Object `
    @{Expression={[int]$_.priority};Descending=$true},`
    @{Expression={if($_.next_eligible_at){[DateTimeOffset]::Parse($_.next_eligible_at)}else{[DateTimeOffset]::MinValue}};Ascending=$true},`
    @{Expression={$_.task_id};Ascending=$true} | Select-Object -First 1
```

`Invoke-RegistryMutation` must:

```text
validate prior task/generation
-> calculate new immutable-safe task record
-> append WAL record
-> atomic registry snapshot
-> return new task
```

`orchestrator-v1.ps1` obtains named mutex `Global\ScorpFullAutoOrchestratorV1-<machine>-<sid>` and on second instance emits `MUTEX_DENIED` then exits without writing registry state.

If a task is `READY` but lacks exact `next_action`, the only permitted automatic mutation is:

```json
{"state":"WAITING","waiting_reason":"GPT_CONTINUATION_REQUIRED"}
```

- [ ] **Step 4: Run tests and verify GREEN**

Expected markers:

```text
PASS stable-task-id-tiebreak
PASS no-local-reasoning
PASS pause-prevents-new-launch
PASS running-lease-preserved-under-pause
PASS dependency-gate
PASS resource-conflict-gate
PASS unchanged-generation-no-spin
PASS mutex-denies-second-instance
ORCHESTRATOR_V1_SCHEDULER_CONTROL_PASS
```

- [ ] **Step 5: Commit**

```text
feat: add deterministic orchestrator scheduler and controls
```

---

### Task 3: Durable V4 Router and Authoritative Reconciliation

**Files:**
- Create: `scorp-agent/orchestrator/router-v4.ps1`
- Create: `scorp-agent/tests/orchestrator-v1-router-continuation.ps1`

**Interfaces:**
- Consumes: registry/store; exact `next_action` in `scorp.exec/v4` compatible form.
- Produces: `New-V4Lease($Task,$Action)`, `Publish-V4Action($Context,$Task,$Lease)`, `Find-V4IssueByIdentity($Context,$Lease)`, `Read-V4Lifecycle($Context,$Lease)`, `Reconcile-V4Lease($Context,$Task)`.

- [ ] **Step 1: Write failing router tests using fake GitHub adapter**

The module takes a dependency-injected `$Context.GitHub` object exposing scriptblocks:

```powershell
$Context.GitHub.CreateIssue.Invoke($title,$body)
$Context.GitHub.SearchIssues.Invoke($identity)
$Context.GitHub.ReadIssue.Invoke($issueNumber)
$Context.GitHub.ReadComments.Invoke($issueNumber)
```

Tests cover:

```text
lease persisted before CreateIssue
lost CreateIssue response reconciles existing matching issue instead of creating second
0 matches after ambiguous publication => BLOCKED/AMBIGUOUS_PUBLICATION
>1 matches => BLOCKED/DUPLICATE_V4_ISSUE
claim/result must bind task_id + action_id + issue + claim_token
terminal result is applied once by lease generation
```

- [ ] **Step 2: Run tests and verify RED**

Expected: router file/function missing.

- [ ] **Step 3: Implement minimal router**

`New-V4Lease` includes:

```powershell
[ordered]@{
  orchestrator_task_id=$Task.task_id
  orchestrator_generation=$Task.generation
  v4_task_id="orch/$($Task.task_id)/g$($Task.generation)"
  v4_action_id=[string]$Action.action_id
  envelope_hash=Get-Sha256Text ($Action|ConvertTo-Json -Depth 64 -Compress)
  issue_number=$null
  claim_token=$null
  publication_phase='PREPARED'
  result_identity=$null
}
```

Publication rule:

```text
persist lease PREPARED
-> publish exactly one [SCORP_EXEC] issue
-> persist issue_number/PUBLISHED when response is certain
-> on error/ambiguity search by stable v4_task_id+v4_action_id+envelope_hash
-> exactly one match: adopt
-> zero/multiple: fail closed, never blind retry
```

The router must not call `codex`, execute action payloads locally, or inspect stdout to choose a next command.

- [ ] **Step 4: Run tests and verify GREEN**

Expected:

```text
PASS publication-lease-before-network
PASS ambiguous-publication-adopts-single-match
PASS ambiguous-publication-zero-blocks
PASS duplicate-v4-issue-blocks
PASS lifecycle-identity-bound
PASS terminal-result-once
PASS router-no-codex
ORCHESTRATOR_V1_ROUTER_PASS
```

- [ ] **Step 5: Commit**

```text
feat: add durable v4 publication and reconciliation router
```

---

### Task 4: Continuation Outbox, GitHub Request Transport, Decision Binding

**Files:**
- Modify: `scorp-agent/orchestrator/continuation-v1.ps1` (create in this task)
- Extend: `scorp-agent/tests/orchestrator-v1-router-continuation.ps1`

**Interfaces:**
- Consumes: Task 1 continuation schema, Task 3 injected GitHub adapter.
- Produces: `New-ContinuationRequest($Task,$Result,$Question)`, `Publish-ContinuationRequest($Context,$Request)`, `Read-ContinuationDecision($Context,$Request)`, `Apply-ContinuationDecision($Root,$Task,$Decision)`, `Finalize-ContinuationRequest($Context,$Request,$Decision)`.

- [ ] **Step 1: Write failing continuation tests**

Tests assert:

```text
exactly one [SCORP_CONT_REQ] issue per request_id
request issue is never titled [SCORP_EXEC]
decision author must equal configured trusted actor
decision binds decision_id/request_id/task_id/expected_registry_sequence/previous result identity
duplicate decision comments => BLOCKED
stale registry generation => BLOCKED
decision applied once even if cloud close/title acknowledgement is lost
cleanup retry never reapplies registry mutation
```

Include fixture decision:

```json
{
  "protocol_version":"scorp.orchestrator/continuation-decision-v1",
  "decision_id":"d-001",
  "request_id":"r-001",
  "task_id":"task-a",
  "expected_registry_sequence":7,
  "previous_action_id":"a-001",
  "previous_result_sha256":"abc",
  "previous_continuation_generation":0,
  "expires_at":"2026-09-11T00:00:00Z",
  "mutation":{"kind":"SET_NEXT_ACTION","next_action":{"protocol_version":"scorp.exec/v4","action_id":"a-002","action_kind":"health"}}
}
```

- [ ] **Step 2: Run tests and verify RED**

Expected: continuation functions missing.

- [ ] **Step 3: Implement minimal continuation protocol**

Cloud request title must be exactly:

```text
[SCORP_CONT_REQ] <request_id>
```

Decision comment framing:

```text
SCORP_CONT_DECISION
<canonical decision JSON>
```

Accepted decision is first written UTF-8 no-BOM to:

```text
C:\ScorpAgent\orchestrator-v1\continuation-inbox\<request-id>.json
```

then applied through `Invoke-RegistryMutation`. Persist `decision_id`, decision hash, and expected sequence before cloud cleanup. Cleanup changes title to `[SCORP_CONT_APPLIED] <request_id>` and closes the issue; failure sets `continuation_cleanup_pending=true` and only cleanup is retried.

- [ ] **Step 4: Run tests and verify GREEN**

Expected:

```text
PASS continuation-request-non-executable
PASS continuation-request-exactly-once
PASS untrusted-decision-blocked
PASS duplicate-decision-blocked
PASS stale-decision-blocked
PASS decision-applied-once
PASS cleanup-retry-idempotent
ORCHESTRATOR_V1_CONTINUATION_PASS
```

- [ ] **Step 5: Commit**

```text
feat: add gpt continuation request and decision protocol
```

---

### Task 5: End-to-End Tick, Result Mapping, Failure/No-Spin, Recovery

**Files:**
- Modify: `scorp-agent/orchestrator-v1.ps1`
- Modify: `scorp-agent/orchestrator/registry-v1.ps1`
- Modify: `scorp-agent/orchestrator/router-v4.ps1`
- Modify: `scorp-agent/orchestrator/continuation-v1.ps1`
- Create: `scorp-agent/tests/orchestrator-v1-recovery-deployment.ps1`

**Interfaces:**
- Produces: one complete `Invoke-OrchestratorTick` state-machine iteration and `Recover-OrchestratorState($Context)`.

- [ ] **Step 1: Write failing state-machine/recovery tests**

Cover exact transitions:

```text
READY + exact action -> RUNNING lease before publication
RUNNING + V4 SUCCEEDED + pre-authored next action -> READY next generation
RUNNING + V4 SUCCEEDED + no next action -> WAITING/GPT_CONTINUATION_REQUIRED + one request
RUNNING + V4 FAILED -> WAITING/GPT_CONTINUATION_REQUIRED unless exact retry policy exists
RUNNING + V4 TIMED_OUT -> no blind rerun
RUNNING + V4 BLOCKED -> BLOCKED
restart with linked V4 RUNNING -> adopt monitor, no second issue
restart with terminal V4 result -> finalize once
restart with ambiguous publication -> BLOCKED
unchanged WAITING/FAILED generation -> no repeated GitHub writes
```

- [ ] **Step 2: Run tests and verify RED**

Expected: integrated tick/recovery assertions fail.

- [ ] **Step 3: Implement minimal integrated state machine**

`Invoke-OrchestratorTick` order:

```text
acquire/reuse mutex
-> recover coherent registry if startup
-> read control
-> reconcile one existing RUNNING lease before considering READY work
-> ingest one valid continuation decision if present
-> if paused: write health, launch nothing
-> select one deterministic READY task
-> persist RUNNING lease
-> route exact action to V4
-> persist/reconcile result
-> apply authored continuation or request GPT
-> write health
```

No path may publish a new V4 issue while an unresolved RUNNING lease exists for that task generation.

- [ ] **Step 4: Run tests and verify GREEN**

Expected marker:

```text
ORCHESTRATOR_V1_STATE_MACHINE_RECOVERY_PASS
```

- [ ] **Step 5: Commit**

```text
feat: integrate orchestrator tick and restart recovery
```

---

### Task 6: Commit-Pinned Deployment + Separate Scheduled Task

**Files:**
- Create: `scorp-agent/orchestrator/bootstrap-orchestrator-v1.ps1`
- Create: `scorp-agent/orchestrator/release-manifest-orchestrator-v1.json`
- Extend: `scorp-agent/tests/orchestrator-v1-recovery-deployment.ps1`

**Interfaces:**
- Deployment input: exact `-CommitSha <40-hex>`.
- Deployment output: `ORCHESTRATOR_V1_BOOTSTRAP_PASS`, installed path, evidence path.

- [ ] **Step 1: Write failing deployment regressions**

Static/runtime gates:

```text
CommitSha required and 40-hex validated
all artifacts fetched by ?ref=<CommitSha>
manifest Git blob identities verified
WinPS5.1 parser checks every .ps1 before task mutation
existing incompatible ScorpFullAutoOrchestrator task fails closed
compatible prior task captured for rollback
new task principal is current intended Interactive user
task action points only to orchestrator-v1.ps1
ScorpComputerAgent action/principal remain unchanged
post-start mutex probe denies second orchestrator
rollback restores/removes prior orchestrator task/install on post-mutation failure
zero new Codex process from bootstrap
```

- [ ] **Step 2: Run tests and verify RED**

Expected: bootstrap/manifest missing.

- [ ] **Step 3: Implement minimal rollback-safe bootstrap**

Install path:

```text
C:\ScorpAgent\full-auto-orchestrator-v1\
```

State root remains:

```text
C:\ScorpAgent\orchestrator-v1\
```

Scheduled task action:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ScorpAgent\full-auto-orchestrator-v1\orchestrator-v1.ps1
```

The bootstrap must verify `ScorpComputerAgent` before/after snapshots are byte/field-equivalent for principal/action/settings relevant to V4.

- [ ] **Step 4: Run deployment regression GREEN on WinPS5.1**

Expected:

```text
ORCHESTRATOR_V1_DEPLOYMENT_REGRESSION_PASS
```

- [ ] **Step 5: Commit and refresh release manifest**

```text
feat: add rollback-safe orchestrator deployment
release: refresh orchestrator v1 manifest
```

---

### Task 7: Real Windows Phase A-D Acceptance

**Files:**
- Create/extend: `scorp-agent/tests/orchestrator-v1-acceptance.ps1`
- No main/V4 production modification.

**Interfaces:**
- Uses the exact commit-pinned bootstrap candidate from Task 6.

- [ ] **Step 1: Bootstrap exact orchestrator release on real Windows**

Expected:

```text
ORCHESTRATOR_V1_BOOTSTRAP_PASS
Task: ScorpFullAutoOrchestrator
ExecutorDependency: 80e41e183441a8498d5ba943cdc8b976b8fbb3a8
```

- [ ] **Step 2: Verify READY pickup <=5 minutes**

Create a safe task with an already-authored `health` next action and record `ready_at`. Acceptance requires `running_at-ready_at <= 300s`.

- [ ] **Step 3: Verify 3+ sequential pre-authored tasks without user continuation**

Seed A/B/C with dependencies and exact harmless actions:

```text
A health -> DONE
B depends A, bounded file_read -> DONE
C depends B, process echo SCORP_ORCH_ACCEPT -> DONE
```

No user message is allowed between task activation and C terminalization.

- [ ] **Step 4: Verify restart during RUNNING**

Use a safe long deterministic V4 action, restart only `ScorpFullAutoOrchestrator`, and prove:

```text
same orchestrator task generation
same V4 issue/action/claim
no second [SCORP_EXEC] issue
one terminal result
```

- [ ] **Step 5: Verify failed-task no-spin and pause**

A deterministic failing action reaches WAITING/BLOCKED once; >=30 seconds produces no duplicate request/issue. Set `paused=true`, create READY task, verify no launch; clear pause and verify pickup.

- [ ] **Step 6: Verify checkpoint boundary gate**

Seed a >30-minute-flow fixture with a checkpoint deadline and assert health detects missing/expired checkpoint without inventing one.

- [ ] **Step 7: Verify zero-new-Codex and health coherence**

Capture Codex PID baseline before acceptance and compare after. Health must match registry current task/sequence and scheduled task PID.

- [ ] **Step 8: Post evidence to #14**

#14 remains not-GREEN until Task 8 unattended GPT bridge acceptance passes.

---

### Task 8: Unattended GPT-5.6 Sol Continuation Bridge + Final Acceptance

**Files:**
- Extend: `scorp-agent/orchestrator/continuation-v1.ps1` only if acceptance exposes transport defects.
- Add: `docs/superpowers/plans/2026-09-10-full-auto-orchestrator-v1-bridge-acceptance.md` only if product-surface setup requires a separately documented operator step.
- Do not add a local reasoning implementation.

**Interfaces:**
- Input: open `[SCORP_CONT_REQ] <request_id>` issue.
- Output: exactly one trusted `SCORP_CONT_DECISION` comment produced by a verifiably configured GPT-5.6 Sol unattended host.

- [ ] **Step 1: Configure an unattended supervisor only on a surface that can verify GPT-5.6 Sol**

Acceptance evidence must record:

```text
host surface
model/config identity = GPT-5.6 Sol
GitHub connection identity
schedule/event trigger
request issue number
run timestamp
```

If available scheduled automation cannot expose/guarantee Sol identity, do not claim this step complete; use an eligible Work surface or leave Phase E BLOCKED rather than silently substitute a model.

- [ ] **Step 2: Create a real continuation request from Task A**

Task A ends `SUCCEEDED` with no pre-authored next action. Orchestrator must create exactly one `[SCORP_CONT_REQ]` and move A to WAITING.

- [ ] **Step 3: Allow unattended GPT-5.6 Sol host to decide without user chat interaction**

The decision must author exact child Task B or exact next action bound to the current request/generation/result.

- [ ] **Step 4: Verify local orchestrator applies decision exactly once**

Required evidence:

```text
one request issue
one trusted decision comment
one local validated inbox artifact
one registry generation advance
request terminalized [SCORP_CONT_APPLIED]
no duplicate application after >=30s quiet
```

- [ ] **Step 5: Verify Task B executes automatically through V4**

Task B must become READY and complete without the user typing `继续` or keeping this chat open.

- [ ] **Step 6: Verify adapter scope truthfully**

PowerShell/file adapters must pass. Playwright and Windows-MCP/Computer Use are GREEN only if their actual deterministic bridge is available and tested; otherwise #14 remains partially BLOCKED and the missing adapter is recorded explicitly rather than simulated.

- [ ] **Step 7: Final #14 GREEN gate**

All required acceptance evidence:

```text
V4 dependency remains 80e41e...
3+ sequential local tasks
restart no duplicate
failed task no-spin
pause works
READY pickup <=5m
checkpoint boundary detection
one unattended GPT-5.6 Sol continuation A->B
health coherent
zero new Codex launches
no unverified adapter claims
```

Only then close #14 as `[SCORP_DONE]` and begin dedicated private control-repository migration. #15 remains master backlog until migration acceptance completes.
