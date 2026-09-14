# Full Auto Orchestrator V1 Design

## Status

Design for issue #14. This subsystem starts only after GPT-native Scorp Executor V4 issue #16 is GREEN. The accepted Windows execution plane is V4.1 release `80e41e183441a8498d5ba943cdc8b976b8fbb3a8`.

The orchestrator is a new subsystem on branch `scorp-full-auto-orchestrator-v1`. It must not modify `main`, the accepted `scorp-gpt-native-v4` release branch, or the production V4 executor/runner contract while being developed.

## Purpose

Allow unfinished work to continue without depending on the user typing `继续` or keeping a chat window open, while preserving one architectural rule: GPT is the only reasoning, planning, coding, debugging, and continuation intelligence layer.

Windows remains deterministic. The local orchestrator schedules already-authored actions, persists state, applies dependency and safety rules, invokes V4, observes evidence, and requests GPT continuation when new reasoning is required. It never invents commands, code, research strategies, or next steps.

## Non-goals

V1 does not:

- replace V4's exactly-once action execution, claim/result identity, timeout, child identity, mutex, or action ledger;
- introduce a local reasoning model;
- call Codex or use Codex as fallback;
- use GitHub as the only scheduler state store;
- execute issue #15 directly; #15 is master backlog metadata;
- migrate to a dedicated control repository before orchestrator acceptance passes;
- silently perform approval-gated external sends, payments, account-security changes, destructive deletion, or irreversible production changes.

## Architecture

`GPT-5.6 Sol -> durable continuation/task records -> Local Orchestrator V1 -> GPT-native Executor V4.1 -> deterministic action -> durable evidence -> Local Orchestrator -> GPT continuation when required`

The architecture separates three planes.

### Intelligence plane

GPT-5.6 Sol authors goals, decomposes work, interprets evidence, creates exact next actions or child tasks, and resolves unexpected outcomes. No local component may substitute for this function. Production acceptance must verify that the unattended continuation host is actually configured to use GPT-5.6 Sol; merely proving that an unspecified ChatGPT task executed is insufficient.

### Scheduling plane

`orchestrator-v1.ps1` performs deterministic task selection, dependency checking, eligibility timing, pause control, durable state transitions, V4 issue creation/observation, and health reporting.

### Execution plane

V4.1 executes exact PowerShell/process/file/Git/health actions and returns identity-bound evidence. The orchestrator treats V4 as the authority for action-level exactly-once semantics.

## Local State Layout

Root: `C:\ScorpAgent\orchestrator-v1\`

- `registry.json` — atomic current registry snapshot.
- `journal.jsonl` — append-only transition WAL.
- `control.json` — pause/emergency scheduling control.
- `health.json` — atomic health snapshot.
- `continuation-outbox.jsonl` — append-only requests for GPT reasoning.
- `continuation-inbox\<request-id>.json` — locally materialized, validated GPT continuation decisions.
- `tasks\<task-id>.json` — bounded per-task extended evidence/checkpoint material when it does not belong in the main snapshot.
- `locks\` — lock metadata only; process exclusion is enforced by a named mutex.

The registry is single-writer. All authoritative mutations are serialized by the orchestrator mutex.

## Persistence Model

The source of truth for current local scheduling state is the atomic registry snapshot plus replayable WAL. GitHub is the cloud mirror/audit/control surface, not the only runtime state store.

For every state mutation:

1. Validate the proposed transition against the task schema and transition rules.
2. Append a WAL entry containing sequence, prior state/hash, new state/hash, mutation reason, task/action identity, and timestamp.
3. Flush the WAL record durably.
4. Atomically replace `registry.json` with the new snapshot using temp-write + flush + rename/replace.
5. Update bounded health/mirror state after the authoritative local mutation.

On startup, the orchestrator loads the snapshot, validates its schema/hash metadata, replays any journal records newer than the snapshot sequence, and fails closed on an unreconcilable sequence/hash fork.

No truncation or compaction occurs until a separate compacted snapshot has been written and verified. V1 may retain the full journal during acceptance.

## Task Record Contract

Each task record contains at least:

- `protocol_version`
- `task_id`
- `project_id`
- `priority`
- `state`
- `dependencies`
- `dependency_policy`
- `parallel_safety`
- `execution_adapter`
- `next_action`
- `continuation_request_id`
- `checkpoint`
- `attempts`
- `max_attempts`
- `next_eligible_at`
- `created_at`
- `updated_at`
- `last_result`
- `evidence_refs`
- `safety_class`
- `authorization_requirement`
- `waiting_reason`
- `blocker`
- `lease` / active action identity when RUNNING

`task_id` is immutable. A task mutation that changes immutable identity is rejected.

## Task State Machine

Primary states:

`STAGED -> READY -> RUNNING -> DONE | FAILED | WAITING | BLOCKED`

Controlled transitions include:

- `STAGED -> READY` when dependencies and activation conditions are satisfied.
- `WAITING -> READY` only when the stated wait condition is satisfied and the next exact action exists.
- `RUNNING -> WAITING` for a non-ambiguous retryable external condition such as rate/network delay, with explicit `next_eligible_at`.
- `RUNNING -> BLOCKED` for ambiguous execution outcome, exhausted safety requirement, schema conflict, unreconcilable ledger state, or missing required authorization.
- terminal states do not transition automatically.

A failed action does not automatically retry merely because attempts remain. Retry requires either a previously authored deterministic retry policy that is safe for that exact action or a new GPT continuation decision.

## Runnable Task Rule

A task is locally runnable only when all of the following are true:

- state is `READY`;
- all required dependencies are satisfied;
- `next_eligible_at` is absent or not in the future;
- pause control permits new launches;
- safety/authorization requirements are already satisfied;
- `next_action` is a complete exact deterministic action or a validated reference to one;
- no conflicting RUNNING task holds the same declared mutable resource scope;
- task/action identity is not already terminal in local or V4 durable evidence.

If new reasoning is needed and there is no exact `next_action`, the task must not remain READY. It becomes `WAITING` with `waiting_reason=GPT_CONTINUATION_REQUIRED`.

## Deterministic Scheduler

The scheduler owns no intelligence. Selection is deterministic:

1. collect runnable READY tasks;
2. sort by priority descending;
3. then by `next_eligible_at` / creation eligibility;
4. then by stable `task_id` lexical tie-breaker;
5. choose the first task that does not conflict with an active mutable resource scope.

The local poll/wake interval targets seconds and must remain comfortably below five minutes. Acceptance requires a READY task to be picked up within five minutes under normal local conditions.

By default V1 runs one action at a time. Parallel execution is allowed only when every involved task explicitly declares `parallel_safety=true` and disjoint mutable resource scopes. Parallelism is not required for V1 GREEN.

## V4 Execution Router

The orchestrator does not execute action payloads itself. It converts an already-authored `next_action` into the accepted V4 control issue/envelope and persists the linkage before publication.

The orchestration lease records:

- orchestrator task id;
- continuation generation;
- V4 task id;
- V4 action id;
- expected envelope hash;
- GitHub issue number once known;
- V4 claim token once observed;
- expected result identity;
- publication/reconciliation phase.

Publication follows the same durable-before-network principle used by V4. After a success or ambiguous network response, the orchestrator searches/reconciles by stable identities before any retry. It must never create a second V4 action merely because an API response was lost.

V4 remains authoritative for action-level child process identity, timeout kill safety, action idempotency, claim/result exactly-once, terminal result evidence, and mutex exclusion.

## Result Classification

The orchestrator maps a verified V4 result into task state using explicit semantics:

- `SUCCEEDED`: apply the GPT-authored postcondition/continuation contract. If another exact action is already persisted, advance it and return READY. If no exact continuation exists, request GPT continuation.
- `PRECONDITION_FAILED`: normally `WAITING` for GPT continuation unless an exact safe alternative was pre-authored.
- `TIMED_OUT`: no blind rerun; request GPT continuation or BLOCK if action outcome may be ambiguous.
- `FAILED`: request GPT continuation unless a deterministic pre-authorized retry rule applies.
- `BLOCKED`: propagate task `BLOCKED` with evidence.

The orchestrator never infers business meaning from stdout/stderr to manufacture a next action. Interpretation belongs to GPT-5.6 Sol.

## GPT Continuation Protocol

When reasoning is required, the orchestrator writes a durable continuation request to `continuation-outbox.jsonl`, journals the request generation, and marks the task `WAITING` with `waiting_reason=GPT_CONTINUATION_REQUIRED`.

A request contains:

- `request_id`
- task/project identity
- current checkpoint and prior decision generation
- latest verified V4 result identity and bounded evidence references
- allowed safety scope
- explicit question/decision required from GPT
- current registry sequence/hash
- expiry/staleness constraints

### Cloud transport

The cloud transport is GitHub and is explicitly non-executable by V4.

The local orchestrator mirrors each pending request to exactly one GitHub issue titled:

`[SCORP_CONT_REQ] <request-id>`

The issue body contains the canonical continuation-request JSON. The request publication phase is persisted before the GitHub write. If creation returns an ambiguous network result, the orchestrator searches authoritatively by `request_id` before retrying. Multiple matching request issues are an incident and cause BLOCKED state.

The unattended GPT-5.6 Sol supervisor reads open `[SCORP_CONT_REQ]` issues and, after reasoning, writes exactly one decision comment with this framing:

`SCORP_CONT_DECISION`

followed by canonical continuation-decision JSON. The cloud supervisor does not create `[SCORP_EXEC]` issues itself and does not execute Windows commands.

The local orchestrator authoritatively reads the request issue and comments, requires a trusted configured GitHub actor, validates the decision identity, then materializes the accepted decision to `continuation-inbox\<request-id>.json` before applying it to the registry. The local inbox is therefore a validated cache/ingress artifact, not a second cloud source of truth.

After application, the orchestrator records the decision id/hash in the task/WAL, verifies the registry mutation, then attempts and authoritatively verifies transition of the cloud request to terminal title `[SCORP_CONT_APPLIED]` and closes it. If that cloud acknowledgement/cleanup cannot be verified, local state records `continuation_cleanup_pending=true` and retries only the idempotent terminalization/cleanup operation; it never reapplies the decision. A lost acknowledgement cannot cause the same decision to be applied twice because `request_id + decision_id + expected_registry_generation` is durable locally.

Duplicate matching decision comments, an untrusted author, or a stale/mismatched decision cause BLOCKED state. V1 does not require a cryptographic signature; integrity comes from trusted GitHub authorization plus exact identity/hash/generation binding and authoritative reconciliation.

### Decision binding

A continuation decision must bind at minimum:

- `decision_id`
- `request_id`
- `task_id`
- expected registry sequence/hash or generation
- previous V4 task/action/result identity when applicable
- previous continuation generation
- decision timestamp/expiry
- requested state mutation

Stale or mismatched decisions fail closed.

A continuation decision may only:

- mark the task terminal;
- author the next exact action;
- create exact child task/dependency records;
- set WAITING with a deterministic wake condition;
- set BLOCKED with a reason.

## Unattended GPT Host Bridge

The local orchestrator cannot and must not impersonate GPT. Unattended reasoning therefore requires a separate GPT-host trigger.

V1 baseline uses a cloud ChatGPT Scheduled supervisor, when that surface supports the required connected GitHub access and GPT-5.6 Sol configuration for the user's account, to periodically inspect open `[SCORP_CONT_REQ]` GitHub requests, reason over pending requests, and write bound `SCORP_CONT_DECISION` comments. This supervisor is not the primary scheduler and does not execute Windows actions directly; it only supplies GPT decisions. Current ChatGPT recurring-task cadence is limited to once per hour, so no sub-five-minute reasoning SLA is claimed for this baseline.

The unattended bridge must record verifiable model identity/configuration evidence. If the selected Scheduled/Work surface cannot pin or verify GPT-5.6 Sol, that surface is not acceptable for #14 GREEN even if it can execute background tasks. The system remains WAITING/BLOCKED for a compatible GPT-5.6 Sol host rather than silently substituting another model.

An optional lower-latency fast path may use a supported ChatGPT Work event-triggered GitHub workflow when the required GitHub pull-request event semantics are available and acceptance-tested. Work currently exposes GPT-5.6 Sol as a selectable model, but the exact event task must still be verified on the user's actual product surface. That fast path may mirror a continuation request into a dedicated PR-control surface, but it is a separate transport adapter. The design must not depend on an undocumented generic issue webhook.

Therefore two separate timing guarantees apply:

- Local READY pickup: <=5 minutes when an exact next action already exists.
- New GPT reasoning latency: governed by the configured GPT-host trigger; V1 hourly baseline does not promise <=5 minutes.

Full #14 production GREEN requires proof that at least one unattended GPT-5.6 Sol host bridge can consume a continuation request and durably create the next decision without the user keeping a chat open or typing `继续`.

## Continuation and 30-minute Boundary

Every GPT-authored chain must end each reasoning turn in one of these durable states:

- terminal task;
- exact next action already persisted;
- exact child task/dependency already persisted;
- WAITING/BLOCKED with explicit reason and wake condition.

For a flow expected to exceed 30 minutes, GPT must author a durable checkpoint/continuation deadline before that boundary. The orchestrator detects impending or actual boundary violation and records health/error state; it never invents the checkpoint itself.

A long deterministic action may remain RUNNING beyond 30 minutes if V4 has durable identity/heartbeat and the surrounding task already has a checkpoint describing how to continue after its result.

## Pause and Emergency Control

`control.json` supports at minimum:

- `paused`: stop launching new actions while preserving active work;
- `pause_reason`;
- `updated_at`;
- `emergency_stop_requested`.

Pause never corrupts or discards a RUNNING lease. Normal pause allows the active V4 action to finish and prevents the next launch.

Emergency stop stops new scheduling immediately. It does not blindly kill an action whose outcome would become ambiguous. Termination of an active deterministic child is delegated to an explicit GPT-authorized V4 action or to an already-defined safe stop policy.

## Safety and Authorization

The orchestrator enforces task-level authorization before publishing an action.

Always approval-gated unless the user has already provided task-specific authorization:

- external outreach sends;
- payments or transfers;
- account-security changes;
- destructive deletion;
- irreversible production changes.

Safe autonomous scope may include research, coding, diagnostics, tests, local file generation, Git branch/commit work, and read-only connector work when the exact action is GPT-authored and the underlying tool authorization permits it.

A task cannot upgrade its own authorization class. GPT continuation cannot override a missing human authorization token by wording alone.

## Failure and No-spin Rules

Every failure records an evidence-bound state transition. The scheduler must not repeatedly reconsider a task with unchanged failure generation.

- Network/rate-limit with known retry time -> WAITING + `next_eligible_at`.
- Schema/precondition issue requiring thought -> WAITING/GPT_CONTINUATION_REQUIRED.
- Ambiguous action publication/result -> BLOCKED.
- Safety/authorization gap -> BLOCKED or WAITING_AUTHORIZATION.
- Max attempts reached -> FAILED or BLOCKED according to the authored policy.

A failed task must not generate repeated GitHub comments/issues every poll cycle. Durable generation/ledger fields suppress re-publication until state materially changes.

## Recovery

On orchestrator restart:

1. acquire named mutex or exit with `MUTEX_DENIED`;
2. recover snapshot + WAL;
3. inspect any RUNNING lease;
4. reconcile the linked V4 issue/action/result authoritatively;
5. reconcile any pending continuation request/decision by request/decision identity;
6. if V4 result is already terminal, finalize the task without rerunning;
7. if V4 action is still RUNNING, adopt monitoring;
8. if publication/execution/continuation outcome cannot be proven, BLOCK rather than duplicate;
9. resume scheduling only after registry coherence is established.

The orchestrator does not own the V4 child process and never guesses from local PID absence that an action is safe to rerun.

## Health Telemetry

`health.json` includes at least:

- protocol/version;
- orchestrator PID/start time;
- mutex owner identity;
- heartbeat timestamp;
- paused/emergency state;
- registry sequence/hash;
- queue counts by state;
- current task/action/linked V4 issue;
- last verified result;
- last continuation request/decision;
- next local wake time;
- oldest READY age;
- oldest GPT_CONTINUATION_REQUIRED age;
- GitHub reachability state;
- V4 executor health observation;
- Remote Desktop Commander observation when available;
- Playwright/Windows-MCP capability observations when available;
- Codex process baseline/current/new count for acceptance diagnostics;
- unattended GPT host surface/model identity/configuration evidence.

Health is observational and must not become an independent source of task truth.

## Scheduled Task and Deployment

The orchestrator uses a separate Scheduled Task named `ScorpFullAutoOrchestrator` under the same intended interactive user context. It must not replace `ScorpComputerAgent`. If an existing task with that name has an incompatible action, principal, or deployment identity, bootstrap fails closed rather than overwriting it blindly.

Deployment must be commit-pinned, rollback-safe, principal-verified, and self-test before mutation. The accepted V4 production task remains untouched except for actions intentionally routed through its public GitHub control protocol.

Orchestrator deployment is not production-approved until Windows PowerShell 5.1 syntax/self-tests and real-machine acceptance pass.

## Source Layout

Planned V1 implementation boundaries:

- `scorp-agent/orchestrator-v1.ps1` — process loop and deterministic scheduling state machine.
- `scorp-agent/orchestrator/task-schema-v1.json` — task contract.
- `scorp-agent/orchestrator/continuation-schema-v1.json` — request/decision contract.
- `scorp-agent/orchestrator/store-v1.ps1` — snapshot/WAL/recovery primitives.
- `scorp-agent/orchestrator/router-v4.ps1` — V4 publication/reconciliation only.
- `scorp-agent/orchestrator/continuation-github-v1.ps1` — `[SCORP_CONT_REQ]` request/decision mirror and reconciliation.
- `scorp-agent/orchestrator/health-control-v1.ps1` — health/control state.
- `scorp-agent/orchestrator/bootstrap-orchestrator-v1.ps1` — isolated install/scheduled-task deployment.
- `scorp-agent/tests/orchestrator-v1-*.ps1` — regression/acceptance gates.

If implementation shows `orchestrator-v1.ps1` becoming a large multi-responsibility file, responsibilities stay in modules rather than being folded into the executor.

## Implementation Phases

### Phase A — contract/store/control

Task + continuation schemas, atomic snapshot, WAL replay, transition validation, named mutex, health, pause, no-spin generation markers.

### Phase B — scheduler/V4 router

READY selection, dependency/eligibility/resource rules, durable publication lease, V4 issue creation/reconciliation, result mapping, ambiguous outcome fail-closed.

### Phase C — continuation chain

Continuation outbox/inbox binding, GitHub `[SCORP_CONT_REQ]` transport, pre-authored deterministic multi-step chains, GPT continuation request generation, trusted-author validation, duplicate/stale-decision rejection.

### Phase D — deployment/recovery

Separate Scheduled Task, restart adoption, pause/emergency semantics, <=5-minute READY pickup, failed-task no-spin, checkpoint boundary enforcement.

### Phase E — unattended GPT bridge and adapters

Background GPT-5.6 Sol supervisor acceptance with model identity evidence; PowerShell/file route acceptance; Playwright and Windows-MCP/Computer Use acceptance where those adapters are available; zero Codex launches.

### Phase F — control repository migration

After #14 is GREEN, migrate registry/control from `Scorp96/666` to a dedicated private control repository. Keep `666` as fallback until migration acceptance proves continuity and rollback.

## Acceptance

#14 may close GREEN only with fresh evidence for all applicable gates:

1. V4.1 remains GREEN and unchanged as execution plane.
2. Task schema, continuation schema, snapshot/WAL replay, atomic writes, and recovery self-tests pass.
3. Task A completes and an unattended GPT-5.6 Sol-authored durable continuation creates Task B automatically through the defined continuation bridge.
4. At least three sequential tasks complete without user `继续`.
5. Each routed V4 action has exactly one effective orchestrator publication and exactly one V4 result identity.
6. Orchestrator restart during RUNNING adopts/reconciles without duplicate V4 action creation.
7. A failed/precondition task does not spin or republish every poll.
8. Pause prevents new launches without corrupting current RUNNING work.
9. Emergency control fails closed for ambiguous unsafe termination.
10. READY work with an exact action is picked up within five minutes under normal local conditions.
11. A >30-minute flow has a durable pre-boundary checkpoint/continuation contract; violations are surfaced rather than invented locally.
12. PowerShell/file routing passes end-to-end through V4.
13. Playwright and Windows-MCP/Computer Use routes are acceptance-tested when usable in the target Windows environment; unavailable adapters remain explicitly capability-gated and cannot be claimed GREEN.
14. Task/registry health remains coherent through restart and transient GitHub failure.
15. No new Codex process is launched by orchestrator, V4, or the unattended GPT bridge.
16. At least one unattended GPT-host bridge performs a continuation decision without the chat remaining open or the user typing `继续`, and fresh evidence verifies the bridge is actually using GPT-5.6 Sol.
17. Continuation request/decision transport proves one request issue, one accepted decision, trusted author, stale-decision rejection, and no duplicate application after ambiguous acknowledgement.
18. >=30 seconds post-terminal quiet produces no duplicate publication/result/continuation.
19. Production branch/control-repo migration does not occur until all preceding gates are satisfied.

## Stop Conditions

Implementation must stop and report BLOCKED rather than weaken the design if any of these are true:

- a required action outcome is ambiguous;
- V4 accepted invariants would need to be weakened;
- the only way to continue would introduce local autonomous reasoning;
- a GPT continuation decision cannot be identity-bound to the exact pending request;
- an approval-gated action lacks authorization;
- the production bridge cannot run unattended on an actually supported product surface;
- the unattended bridge cannot pin or verify GPT-5.6 Sol;
- required adapter capability is unavailable but would otherwise be falsely claimed as tested.

## Product-surface Constraint Recorded 2026-09-10

Current ChatGPT Scheduled tasks can run recurring work and may use connected apps such as GitHub when available. Their available models vary by task/account/workspace, and regular recurring schedules support hourly cadence on eligible paid plans. ChatGPT Work supports selectable GPT-5.6 Sol and also supports event-triggered tasks for supported GitHub pull-request activity on eligible product surfaces.

V1 therefore treats hourly scheduled supervision as a conservative unattended reasoning baseline only when the actual task can be configured and verified on GPT-5.6 Sol. Supported PR-event triggering is an optional lower-latency fast path after separate verification. If neither surface can provide verified unattended GPT-5.6 Sol continuation on the user's account, #14 remains not-GREEN rather than substituting another model.

These constraints are intentionally separate from the local scheduler's <=5-minute READY pickup requirement.
