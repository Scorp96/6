# D Lifecycle Preemption V3 Design

## Purpose

Scorp Master identity A must continue across bounded ChatGPT execution windows without a user sending `继续`. D must not wait until A is already dead and then improvise recovery. D must know every A window's lifecycle from acquisition time, preserve a deterministic handoff schedule, and start the next A only after proving that doing so cannot create two live masters.

## Design principles

1. **D is deterministic, not a reasoning agent.** It observes immutable project contract, authoritative PROJECT_STATE, and durable master lease state. It never replans or changes the root objective.
2. **A identity is persistent; A windows are disposable.** A1/A2/A3 are bounded sessions for the same controller identity `A`.
3. **The lifecycle is fixed at acquisition.** Heartbeats cannot extend a window beyond its hard lifecycle deadline.
4. **Handoff is proactive.** A receives its lifecycle schedule in the bound turn payload and must checkpoint PROJECT_STATE before the handoff window, then return `DRAIN` by the soft deadline when the project remains ACTIVE.
5. **No split brain.** D must never queue A2 while A1 still owns a live ACTIVE lease before the hard deadline.
6. **Hard takeover is guaranteed.** If A does not DRAIN, D may resume after the fixed hard deadline because the old window can no longer own the authoritative master lease.
7. **Terminal projects never resume.** COMPLETE/HARD_BLOCKED dominate all lifecycle timers.

## Lifecycle model

The existing 1500-second role turn budget remains the default hard lifetime. A new constant `SOFT_HANDOFF_LEAD_SECONDS = 60` defines the final planned handoff boundary.

At master lease acquisition D/lease storage persists:

- `acquired_at`
- `heartbeat_at`
- `lease_until`
- `soft_handoff_at = acquired_at + hard_lifetime - soft_lead`
- `hard_handoff_at = acquired_at + hard_lifetime`
- `hard_lifetime_seconds`
- `soft_handoff_lead_seconds`

`lease_until` remains compatible with the existing lease protocol, but no heartbeat may move it later than `hard_handoff_at`. The hard deadline is therefore monotonic and cannot be renewed away.

Existing legacy leases that do not contain explicit lifecycle fields remain readable. For them, `hard_handoff_at` is derived from their existing `lease_until`; D does not rewrite them merely by reading them.

## A behavior

Every MASTER turn payload includes a `master_lifecycle` object with:

- current session id
- `soft_handoff_at`
- `hard_handoff_at`
- hard lifetime seconds
- soft lead seconds
- `handoff_reserve_seconds`

The bound master prompt tells A:

- checkpoint meaningful progress into PROJECT_STATE throughout the window;
- when the project remains ACTIVE, prepare a safe continuation during the existing handoff reserve;
- return `DRAIN` no later than `soft_handoff_at` rather than consuming the hard deadline;
- never create/launch its successor itself; D owns successor creation.

A normal `DRAIN` keeps durable audit identity and ends A1 ownership. It does not alter the root contract.

## D decision table

For an ACTIVE project:

| Lease state / time | D result | Successor allowed? |
| --- | --- | --- |
| no lease | `NO_ACTIVE_MASTER` | yes, queue A |
| ACTIVE and now < soft | `MASTER_ACTIVE` | no |
| ACTIVE and soft <= now < hard | `HANDOFF_DUE` | no; wait for A to DRAIN |
| ACTIVE and now >= hard | `HARD_DEADLINE_EXPIRED` | yes |
| DRAINED and now < soft | `WAITING_HANDOFF_WINDOW` | no |
| DRAINED and now >= soft | `PLANNED_HANDOFF` | yes |
| RELEASED while project still ACTIVE | `LEASE_ENDED` | yes |
| project COMPLETE/HARD_BLOCKED | terminal status | never |

This gives proactive scheduling without a blind timer. D re-reads PROJECT_STATE and lease state on every tick before creating a successor.

## Deterministic successor identity

`RESUME_MASTER` remains content-addressed. Its deterministic identity includes:

- project id
- state version
- immutable goal/acceptance hashes
- predecessor session/status
- predecessor lifecycle deadlines
- resume reason

Repeated D ticks for the same predecessor and state produce the same turn id/session id and therefore remain exactly-once through the existing outbox conflict checks.

## State and compatibility

PROJECT_STATE remains the semantic authority and is not mutated by D. Lifecycle scheduling is operational state stored in the master lease. `active_master_window` inside PROJECT_STATE remains non-authoritative for ownership.

No schema migration is required for existing project-state files. Master lease protocol remains backward-readable. New fields are additive.

## Failure behavior

- If A returns `DRAIN`, D waits until the planned handoff window and then queues A2.
- If A hangs/stops and never drains, the hard deadline expires and D queues A2.
- If a stale/mismatched GUI HWND exists, transport provenance rules remain fail-closed; lifecycle takeover does not authorize closing an unproven window.
- If the project turns terminal before the handoff time, D returns terminal and emits no successor.
- If a successor outbox file already exists with identical content, D reports `ALREADY_QUEUED`; different content at the same deterministic identity remains a conflict.

## Acceptance criteria

1. Lease acquisition persists fixed soft/hard lifecycle deadlines.
2. Heartbeat never extends the hard deadline.
3. D reports HANDOFF_DUE after soft deadline while old A is still ACTIVE and does not create A2.
4. A DRAIN before soft does not immediately create A2; at/after soft the same state creates exactly one successor.
5. Crossing the hard deadline creates exactly one successor even if old A never drained.
6. MASTER prompt/payload tells A the exact lifecycle schedule and DRAIN obligation.
7. Existing continuation, project-contract, worker, transport and terminal semantics remain green.
8. Live E2E proves `A1 -> D -> A2` without any user message for both planned DRAIN and hard-deadline takeover.
9. A second live master is never observable before the predecessor ownership is ended or hard-expired.
