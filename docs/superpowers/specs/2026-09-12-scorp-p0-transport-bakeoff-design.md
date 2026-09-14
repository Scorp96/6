# Scorp P0 ChatGPT Transport Bake-off Design

Date: 2026-09-12
Status: Design approved for specification; implementation gated on written-spec review
Repository: `Scorp96/666`
Baseline branch: `feature/d-lifecycle-preemption-v3`
Baseline commit: `5e5fdc7ef82aa244d5f29c7468bed56abdc78d57`

## 1. Purpose

Determine whether Scorp should replace its current Windows-MCP/Win32 ChatGPT Web execution path with a browser-native transport while preserving Scorp's existing deterministic control-plane semantics.

The bake-off is intentionally narrow: it changes the execution adapter only. It does not redesign Persistent Master A, deterministic supervisor D, project-state CAS, worker admission, worker lifecycle, evidence, acceptance, or terminal fencing.

## 2. Final goal

Prove a production-candidate ChatGPT Web transport that can autonomously and safely operate the user's already-authenticated ChatGPT browser session while preserving conversation identity and Scorp exactly-once/fail-closed semantics.

The target is not merely successful UI automation. The target is 20 consecutive lifecycle cycles with:

- zero prompt delivery to the wrong conversation;
- zero duplicate prompt submission;
- zero manual `continue` actions;
- deterministic recovery after transport/process interruption;
- correct Persistent A conversation reuse;
- correct Worker conversation-slot reuse;
- fail-closed handling when submission state cannot be proven.

## 3. Authoritative baseline and production isolation

The authoritative implementation baseline is commit `5e5fdc7ef82aa244d5f29c7468bed56abdc78d57` on `feature/d-lifecycle-preemption-v3`.

Production state is out of scope for mutation during P0. In particular:

- do not clear, overwrite, migrate, or repurpose `C:\ScorpAgent\state-v3\active`;
- do not modify the active `production-e2e-g274` project as part of the PoC;
- do not enable the currently disabled production watchdog as part of the PoC;
- do not replace the installed production bridge during P0;
- use an isolated PoC state root and isolated browser/transport configuration;
- preserve forensic evidence from all failures.

## 4. Existing Scorp modules and disposition

### KEEP unchanged in P0

- `project_state_v3.py`
  - immutable project identity/contract fields;
  - CAS `state_version`;
  - terminal-state fencing.

- `continuation_watchdog_v3.py`
  - deterministic D behavior;
  - lease-driven `RESUME_MASTER` generation;
  - no semantic replanning inside D.

- `master_state_transition_v3.py`
  - transition identity hashing;
  - live-owner enforcement;
  - replay repair after durable state commit;
  - authoritative A-only state transitions.

- `worker_result_guard_v3.py`
  - project/root/acceptance/objective alignment checks;
  - state-version and assignment validation;
  - resource-scope enforcement.

- `turn_scheduler_v3.py`
  - recovery/resume/worker-event priority;
  - stale master rejection;
  - active Worker enforcement;
  - terminal supersession.

- `worker_conversation_pool_v3.py`
  - separation of ephemeral logical Worker identity from persistent ChatGPT conversation slots;
  - durable slot leases.

### KEEP semantics, introduce adapter boundary

- `durable_actor_transport_v3.py`
  - preserve durable intent before remote side effects;
  - preserve `SUBMITTING`, `SUBMITTED`, `COMPLETED`, `RETRYABLE`, `TIMED_OUT`, and `AMBIGUOUS` semantics;
  - preserve recovery-before-resubmit behavior;
  - transport backend remains replaceable.

- `actor_gui_backend_v3.py`
  - preserve submit/poll/recover contract where practical;
  - rename/generalize only if required after tests prove the abstraction needs to stop being GUI-specific;
  - no semantic weakening to make a new adapter easier to implement.

- `parallel_master_worker_relay_v3.py`
  - preserve bounded inflight execution, worker resource-scope exclusion, response journal, conversation binding, and routing semantics;
  - transport choice must be injected rather than hard-coded.

### REPLACE candidate

- `windows_mcp_actor_driver_v3.py`
  - current implementation couples ChatGPT conversation identity and execution to HWND focus, foreground Chrome ownership, Windows-MCP stdio sessions, UI-tree snapshots, address-bar inspection, and Win32 window lifecycle;
  - retain it as the control implementation for the bake-off, not as the assumed long-term winner.

## 5. Candidate transports

### Candidate A — Current Windows-MCP driver

Purpose: control baseline.

Advantages:

- already integrated;
- existing test coverage;
- known Scorp semantics.

Risks:

- Windows focus/HWND coupling;
- stdio/MCP lifecycle complexity;
- browser UI tree brittleness;
- current production `TaskGroup` failure remains unresolved.

### Candidate B — browser-native Chrome transport

Primary P0 challenger.

The adapter should control the already-authenticated Chrome instance through a browser-native mechanism, with `chrome-use` as the first reference implementation to evaluate.

Required properties:

- target exact ChatGPT conversation by canonical URL or equally strong stable identifier;
- inspect the active conversation without relying on foreground-window identity;
- submit a prompt once and return a durable remote handle/identity;
- poll generation completion independently from desktop focus;
- enumerate/recover a turn by Scorp turn marker after a local transport restart;
- preserve multiple concurrent remote ChatGPT generations while serializing only browser mutations that truly require serialization;
- expose enough evidence to prove which conversation received a prompt.

### Candidate C — agent/browser abstraction

Fallback candidate only if Candidate B cannot satisfy Scorp's deterministic identity and recovery requirements.

Examples may include Browser Use or another browser agent/runtime. It must not be selected merely for higher-level convenience if it weakens exact conversation targeting, duplicate-submission prevention, or deterministic recovery.

## 6. Adapter contract

P0 should preserve the current backend-facing shape unless evidence proves it insufficient.

Conceptual interface:

```python
async def submit(
    submission_id,
    *,
    prompt,
    turn_id,
    actor_kind,
    conversation_url,
    timeout_seconds,
) -> dict:
    ...

async def poll(handle, *, turn_id, actor_kind, timeout_seconds) -> dict:
    ...

async def recover(submission_id, *, turn_id, actor_kind) -> dict | None:
    ...
```

### `submit` invariants

- Never silently change a supplied canonical conversation target.
- A successful return must include enough identity to prove the remote conversation target.
- If the system cannot know whether the remote side effect occurred, it must not classify the attempt as safely retryable.
- Known pre-generation failures may be classified `RETRYABLE` only when there is evidence that no remote generation was established.

### `poll` invariants

- Must observe exactly the conversation represented by the handle.
- Must not depend on another unrelated Chrome window being focused.
- Must not close or mutate an unrelated tab/window on mismatch.
- A terminal result must contain the Scorp turn marker and a parseable actor response.

### `recover` invariants

- Must never submit a new prompt.
- Returns a handle only when exactly one remote conversation can be proven to correspond to the turn/submission.
- Zero matches -> `None` / ambiguous path according to `DurableActorTransportV3` semantics.
- More than one plausible match -> fail closed as ambiguous.

## 7. Conversation identity rules

Persistent Master identity remains `A` and is distinct from a particular execution window or transport process.

P0 must preserve:

- same canonical A ChatGPT conversation during normal continuation;
- recovery conversation creation only under the existing explicit recovery rules;
- ephemeral logical Worker IDs and sessions;
- durable Worker conversation slots that may be reused by later logical Workers;
- no transport-side invention of a second semantic identity system that conflicts with Scorp's project/turn/session bindings.

## 8. Bake-off harness

The same scenario set must run against Candidate A and Candidate B. Candidate C is added only if B fails a hard requirement.

### Scenario 1 — Persistent A continuation

1. Target an existing canonical A conversation.
2. Submit a structured continuation.
3. Capture conversation identity.
4. Poll to terminal response.
5. Submit the next A turn to the same conversation.
6. Prove no conversation rotation occurred.

### Scenario 2 — Worker creation and slot reuse

1. Allocate Worker alpha and beta.
2. Bind two Worker conversation slots.
3. Complete one Worker and release its lease.
4. Dispatch gamma.
5. Prove gamma reuses the released persistent slot while receiving a distinct logical worker/session/turn identity.

### Scenario 3 — crash after remote submit, before local persistence completes

1. Persist `SUBMITTING` intent.
2. Establish a remote generation.
3. Kill the local adapter before durable handle persistence.
4. Restart.
5. Call `recover`.
6. Prove the existing generation is recovered with zero duplicate prompt submission.

### Scenario 4 — ambiguous recovery

Construct a state where the adapter cannot prove a unique matching remote turn.

Required result: fail closed. No automatic resubmit.

### Scenario 5 — adapter/daemon restart

Restart only the browser transport process while Chrome and ChatGPT remain available.

Required result: current in-flight or completed turn can be reconciled without manual action or prompt duplication.

### Scenario 6 — Chrome restart

Restart Chrome and reconnect using the user's authenticated browser state.

Required result: canonical A and reusable Worker conversations can be retargeted from durable Scorp state. If safe automatic recovery cannot be proven, the outcome must be an explicit blocker/ambiguous state rather than silent rotation or duplicate submission.

### Scenario 7 — concurrent generations

Run one A turn and at least two Worker turns with remote generation overlap.

Required result:

- no cross-conversation response capture;
- no prompt sent to the wrong actor conversation;
- no resource-scope conflict admitted by the Scorp relay;
- browser mutation serialization must not prevent remote-generation concurrency.

### Scenario 8 — throttling/composer unavailable

Required result:

- classify as retryable only when no remote generation was established;
- deterministic defer/cooldown behavior;
- no `SUBMITTING -> recover -> AMBIGUOUS` caused solely by a known pre-generation failure.

### Scenario 9 — stale target

Invalidate a tab/window/browser handle while preserving the canonical conversation URL.

Required result: recover/reacquire by canonical conversation identity without mutating or closing an unrelated browser target.

### Scenario 10 — unattended lifecycle cycle

Execute full:

`A -> DISPATCH -> Workers -> HANDOFF/BLOCKER -> A validate/integrate -> DRAIN -> D -> same A conversation -> TERMINAL/CONTINUE`

No user continuation is permitted.

## 9. Quantitative acceptance gate

A candidate is eligible for V4 integration only after all hard scenarios pass and a 20-cycle soak satisfies:

- wrong-conversation submissions: `0`;
- duplicate submissions: `0`;
- manual continuation interventions: `0`;
- unreconciled conversation-identity changes: `0`;
- stale Worker lease deadlocks: `0`;
- terminal states with transport `SUBMITTING`, `AMBIGUOUS`, `TIMED_OUT`, or retry-storm residue: `0`;
- every Worker result passes the existing result-admission path before becoming semantically usable;
- final project state and evidence are consistent with the existing acceptance contract.

Performance is secondary. A faster candidate that weakens identity, recovery, or exactly-once behavior loses.

## 10. Decision rules

### Select Candidate B when

- all hard scenarios pass;
- the 20-cycle soak passes;
- it materially reduces HWND/focus/MCP coupling;
- it preserves or improves forensic evidence and recovery confidence.

### Retain Candidate A when

- Candidate B cannot prove exact conversation targeting or safe recovery;
- browser-native control introduces weaker identity guarantees than the current implementation;
- Candidate B requires unsupported changes to ChatGPT Web that cannot be made robustly.

### Evaluate Candidate C when

- Candidate B fails for a reason that a higher-level browser runtime can solve without weakening Scorp's deterministic guarantees.

### Stop condition

Do not continue adding abstraction layers after a candidate has passed the hard acceptance gate. P0 exists to select an execution transport, not to create a generic browser framework.

## 11. TaskGroup production blocker

The existing production error `unhandled errors in a TaskGroup (1 sub-exception)` remains a separate root-cause investigation.

P0 must not assume its cause.

Current evidence indicates Scorp's inspected orchestration modules do not explicitly create the reported TaskGroup, while the current Windows transport opens MCP stdio/client sessions. This is only a hypothesis boundary, not a verified root cause.

Before any production fix:

1. capture the complete ExceptionGroup / child exception;
2. identify the exact component boundary that raises it;
3. compare whether the same failure exists under the browser-native transport;
4. fix only the proven root cause if Candidate A remains relevant.

If Candidate B wins the bake-off and the failure belongs exclusively to the retired Windows-MCP path, do not spend additional engineering effort polishing the retired path beyond what is needed to preserve rollback capability.

## 12. P0 implementation boundaries

Implementation may add:

- a transport/driver factory or dependency-injection seam;
- an isolated browser-native driver implementation;
- transport conformance tests;
- bake-off fixtures/harness;
- isolated PoC bootstrap configuration;
- evidence capture required by the acceptance scenarios.

Implementation must not:

- alter root-objective or acceptance hashes;
- weaken `ProjectStateStore` CAS or immutable fields;
- allow Workers to mutate authoritative project state;
- bypass `WorkerResultGuardV3`;
- replace deterministic D with an AI agent;
- silently rotate A conversations;
- change production scheduled tasks during P0;
- mutate the authoritative active production project.

## 13. Rollout after P0

If Candidate B wins:

1. retain Candidate A as rollback transport initially;
2. integrate Candidate B behind the same `DurableActorTransportV3` semantics;
3. run full targeted tests and full regression;
4. run isolated installed-production-equivalent E2E;
5. only then plan a controlled production migration;
6. after production E2E passes, run Windows reboot/logon recovery acceptance;
7. remove old Windows-MCP-specific code only after the new path proves stable and rollback evidence is archived.

## 14. Non-goals

P0 does not introduce Temporal, replace Scorp with UFO Galaxy, migrate to the OpenAI API, redesign the Master/Worker protocol, or optimize token/model cost.

Those may be evaluated after the execution transport is proven.

## 15. Success definition

P0 is successful when evidence, not architectural preference, selects the transport that best satisfies Scorp's root objective: unattended, durable, deterministic continuation of GPT work on the user's computer with exact conversation identity, bounded Worker execution, recoverable state, and zero dependence on the user repeatedly sending `continue`.
