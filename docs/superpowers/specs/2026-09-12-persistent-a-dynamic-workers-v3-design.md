# Persistent A + Dynamic Workers V3 Design

Date: 2026-09-12
Status: Approved architecture

## Goal
Run one persistent Master identity `A` autonomously across many bounded GPT-5.6 Sol reasoning turns, with deterministic supervisor `D` keeping A and ephemeral workers alive, while A dynamically dispatches bounded workers to accelerate independent work, reviews their output, prevents drift, and fuses evidence into one authoritative project state without requiring the user to type `继续`.

## Authority model
- `D` is a deterministic local supervisor. It never reasons about the project, changes the root objective, or accepts worker conclusions.
- `A` is the only reasoning/dispatch authority. There is exactly one persistent Master identity: `A`.
- Worker identities are ephemeral and dynamic (`worker-*`). They may execute only their bound assignment and return structured HANDOFF/BLOCKER evidence. They never mutate root objective/acceptance criteria, dispatch other workers, or publish production actions directly.
- `PROJECT_STATE` + immutable goal/acceptance hashes remain authoritative. Hidden model chain-of-thought is never assumed transferable.

## Persistent A conversation
Normal continuation must reuse the same canonical ChatGPT conversation for A. A bounded GPT reasoning turn ending does not rotate the conversation. The session/turn identity changes internally for audit and split-brain prevention, but `MASTER_IDENTITY=A` and the canonical conversation URL remain stable.

D rotates A to a new conversation only when the prior conversation is provably unusable: missing/invalid mapping, provenance mismatch, unrecoverable GUI failure, hard actor timeout, or explicit context-rotation policy. Rotation must load the latest durable handoff and record the predecessor URL/reason.

## A lifecycle
At A lease acquisition, D records fixed soft/hard lifecycle deadlines. Before the soft deadline A checkpoints meaningful work and returns `DRAIN` if the project remains ACTIVE. D then reactivates the same A conversation for the next reasoning turn. If A crashes or fails to drain, the hard deadline releases ownership and D resumes A from durable state.

`WAIT` means A has no immediate reasoning work but remains the persistent controller. New worker events, lifecycle deadlines, or explicit project events may reactivate the same conversation.

## Dynamic workers
A decides worker count from actual independent subproblems, bounded by `MAX_WORKERS=3` initially. A may choose zero workers for small/sequential work. Workers are created only when assignments are independent enough to run concurrently and their scopes are non-conflicting or read-only.

Each worker assignment binds:
- project/root objective/acceptance hashes
- worker id and objective hash
- exact task and resource scope
- success criteria
- allowed response kinds (`HANDOFF`, `BLOCKER`)
- lifecycle deadline / timeout
- no-dispatch / no-production-publication constraints

Workers are ephemeral: assignment -> execution -> HANDOFF/BLOCKER -> retirement. A receives durable worker events and is reactivated to review them.

## Drift control and fusion
Worker output is evidence, not authority. Before adoption, A checks:
1. assignment/objective/root/acceptance hashes match;
2. scope was not exceeded;
3. claimed tests/evidence exist and are internally consistent;
4. results do not conflict with another worker or authoritative state;
5. risky production changes remain behind deterministic verification gates.

For high-value engineering work, A should prefer heterogeneous roles rather than duplicate voting: investigator, test designer, implementer, reviewer/verifier. Majority agreement is never considered proof.

A fuses accepted worker results into the next durable PROJECT_STATE/handoff and chooses continue/dispatch/wait/drain/terminal. Workers never merge themselves.

## Event-driven supervision
A does not poll workers continuously. Worker completion emits durable HANDOFF/BLOCKER events. D/coordinator observes lifecycle/event state and reactivates A only when there is reasoning work.

D monitors:
- A active/soft/hard deadlines and conversation availability;
- worker execution deadlines and terminal states;
- pending worker events requiring A review.

D may synthesize deterministic timeout/blocker state for expired workers, but never business conclusions.

## Single-writer and split-brain rules
- At most one active A lease.
- At most one GUI submission writer at a time.
- Remote GPT generations may overlap for workers, but desktop input remains serialized.
- A conversation reuse must be provenance-bound to the exact project/master mapping.
- D must never create a second active Master before the predecessor lease is drained/released/hard-expired.

## Initial limits
- `MAX_WORKERS=3` concurrent workers.
- No worker-to-worker delegation.
- No worker direct production publication.
- No automatic root-objective/acceptance mutation.
- Same A conversation is default; new A conversation is recovery-only.

## Acceptance criteria
1. A completes at least three reasoning turns in the same canonical conversation with different internal turn/session ids.
2. No user `继续` message is required between A turns.
3. A dynamically dispatches 0-3 workers based on task decomposition; worker identities are ephemeral and bounded.
4. Two or more disjoint workers can run concurrently; conflicting scopes are serialized or rejected.
5. Worker HANDOFF/BLOCKER events durably reactivate A; A reviews and fuses evidence before state mutation.
6. A rejects stale, wrong-hash, out-of-scope, or conflicting worker output.
7. Worker timeout is deterministic and does not leave permanent inflight state.
8. A soft handoff reactivates the same conversation; hard failure recovery also resumes without user input.
9. New A conversation is created only after a recorded recovery reason and durable handoff restore.
10. Live unattended E2E proves: one startup -> A dispatches workers -> workers finish -> A fuses -> D later reactivates the same A conversation -> project continues/terminates, with no further control command from the initiating chat.
