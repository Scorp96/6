# SCORP V4 Master A Dynamic Workers Design

Date: 2026-09-13
Status: Approved for implementation
Repository: `Scorp96/666`
Baseline branch: `p0/chatgpt-transport-bakeoff`
Baseline commit: `e87a9f70c55622e5ca742284e00918e13552f8ac`
Implementation branch: `feature/v4-master-a-dynamic-workers`
Worktree: `C:\ScorpAgent\worktrees\v4-transaction-core`
Lab state root: `C:\ScorpAgent\v4-core-lab`

## 1. Goal and completion boundary

SCORP V4 runs one persistent logical Master A with dynamically allocated bounded
Workers. A local SQLite database is the only authoritative state store for
contracts, project state, task DAGs, assignments, leases, events, external-action
intents, outbox work, browser bindings, evidence, reviews, and release identity.

The first release supports one Windows host that already has an interactive Windows
login and a valid ChatGPT Web login. It uses the existing browser and ChatGPT
subscription. It must not introduce a paid model API or a new cloud service.

This implementation may produce a code candidate and validate AC01 through AC12.
Per the user's revised acceptance boundary, AC12 is a real short-duration performance
and recovery soak: it measures elapsed time, throughput, two-Worker concurrency,
slot reuse, scheduling recovery, errors, and duplicate submissions. A PASS does not
prove 24-hour stability. Production cutover remains a separate authorization and
real-browser evidence remains separately scoped.

## 2. Isolation and non-goals

All implementation and tests run in the named worktree and lab state root. The task
does not merge, push, deploy, change scheduled tasks, replace the installed bridge,
modify existing browser profiles, change `C:\ScorpAgent\state-v3\active`, change
`C:\ScorpAgent\state-v4`, or switch production.

The existing V3 and older PowerShell runtimes remain available for rollback. V4 may
read them only through explicit migration or compatibility boundaries. V4 does not
write their JSON ledgers after SQLite authority is enabled.

## 3. Architectural choice

V4 is a shadow transaction core added under
`scorp-agent/master_a_dynamic_v4/`. It does not retrofit the V3 JSON stores in
place. Existing Chrome-use code may be wrapped as a browser execution engine, but
its JSON state is not authoritative for V4.

The rejected alternatives are an in-place V3 conversion and wholesale continuation
of the detached `901f03d` draft. The former couples candidate work to production
paths; the latter uses incompatible SQLite settings and lacks the required commit,
outbox, browser ambiguity, migration, and acceptance contracts.

## 4. SQLite authority and durability

Every connection sets and verifies:

```sql
PRAGMA journal_mode=DELETE;
PRAGMA synchronous=FULL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
```

The database includes normalized tables for `contracts`, `project_state`,
`task_nodes`, `task_dependencies`, `assignments`, `leases`, `events`,
`transitions`, `action_intents`, `outbox`, `browser_bindings`,
`candidate_results`, `evidence_receipts`, `review_deadlines`,
`review_findings`, `release_candidates`, `imported_snapshots`, and
`schema_migrations`.

Semantic writes use `BEGIN IMMEDIATE`. Browser, network, GitHub, and subprocess I/O
are forbidden inside database transactions. External work is represented by a
durable intent and outbox row committed before the side effect occurs.

## 5. StateStore commit contract

The public transition method is:

```python
StateStore.commit(
    expected_version: int,
    master_epoch: int,
    transition_id: str,
    proposal: Mapping[str, Any],
    evidence_refs: Sequence[str],
) -> CommitResult
```

`CommitResult` is exactly one of `COMMITTED`, `ALREADY_COMMITTED`,
`VERSION_CONFLICT`, `FENCED`, or `REJECTED`.

Within one immediate transaction, the store validates the root contract, current
epoch, expected state version, proposal schema, evidence ownership, task and lease
rules, and canonical content hash. One transition ID with identical content is
idempotent and returns `ALREADY_COMMITTED`. Reusing it with different content is
`REJECTED`. An old epoch is `FENCED`. A stale version is `VERSION_CONFLICT`. At most
one contender can commit a given state version.

Master A creates proposals but has no direct SQL or completion-signing authority.

## 6. Persistent Master A

The logical identity is always `A`. `master_epoch` fences stale sessions and is
independent from browser URL, tab, process, and transport session. Browser rebind
increments a binding generation and records predecessor, reason, timestamp, and
evidence without changing the logical identity.

A may propose task graphs, assignments, integrations, retries, replans, and a
completion request. Deterministic validators decide whether each proposal is legal.

## 7. Dynamic Worker scheduler

The default concurrency is two reusable slots. Each Worker execution binds four
separate identities: a logical `worker_id`, globally unique `assignment_id`, reusable
`slot_id`, and unguessable `lease_token`.

The scheduler persists excess work. It rejects or defers tasks whose dependencies are
not verified, whose resource scopes overlap active writes, whose epoch is stale,
whose lease is expired or mismatched, or whose normalized paths escape the allowed
worktree and lab roots. Worker results enter `candidate_results` and cannot directly
mutate authoritative project state.

## 8. Browser adapter and exactly-once boundary

The adapter exposes:

```python
submit_once(intent)
reconcile(intent)
rebind(channel)
```

Browser intent states are `PREPARED`, `MAY_HAVE_SUBMITTED`,
`CONFIRMED_SUBMITTED`, `RESPONSE_CAPTURED`, `VERIFIED_NOT_SUBMITTED`, and
`BLOCKED_AMBIGUOUS`.

Before any operation that might submit, one transaction changes the durable intent
to `MAY_HAVE_SUBMITTED`; the transaction commits before browser I/O begins. A second
transaction records the observation afterward. A timeout, missing URL, transport
crash, unknown DOM state, or multiple plausible matches never means "not submitted."
Automatic retry is allowed only after positive evidence establishes
`VERIFIED_NOT_SUBMITTED`.

Windows logged-out, locked/unavailable interactive session, expired ChatGPT login,
CAPTCHA, challenge, verification prompt, or uncertain target produces a durable
blocker. The system never attempts to enter passwords or verification codes.

## 9. Crash recovery

Fault injection covers crashes after transaction commit, dispatch reservation,
`MAY_HAVE_SUBMITTED`, confirmed submit, response capture, result persistence, and
terminal cleanup. Restart logic derives work only from SQLite. It replays idempotent
local steps, reconciles possibly submitted actions without resubmitting, expires or
fences stale leases, preserves conflicting evidence, and never silently deletes an
ambiguous row.

## 10. Deterministic acceptance

Only the acceptance validator may authorize `COMPLETE`. It checks contract hashes,
required task and result verification, zero active Workers, zero unprocessed results,
zero unresolved browser ambiguity, evidence receipts bound to the tested candidate
and artifact hashes, release/install identity, and zero blocking findings.

Empty evidence, a self-asserted PASS, a stale receipt, an active Worker, a pending
`MAY_HAVE_SUBMITTED`, or any `BLOCKED_AMBIGUOUS` row rejects completion. A late review
finding is append-only and can reopen a blocker without rewriting the historical
review verdict.

## 11. JSON migration and exports

JSON is accepted only as an explicit `IMPORTED_SNAPSHOT` or emitted as a read-only
export. Import receipts preserve the original absolute path, exact bytes SHA-256,
size, timestamp, parsed schema, conflicts, and mapping result. The importer does not
invent prior events or assignments. Missing or invalid root contracts remain
`BLOCKED`. After cutover to SQLite authority, V4 does not dual-write JSON.

## 12. Real Git workload

The acceptance workload consists of real Python code and real artifacts:

- T1 strictly reads CSV rows and fails without partial output on malformed input.
- T2 uses `Decimal` for deterministic category aggregation.
- T3 depends on verified T1 and T2 results and emits stable JSON through a CLI.

T1 and T2 are admitted concurrently into two disjoint slots. T3 becomes runnable
only after both dependencies are verified. Evidence includes actual commands, exit
codes, output hashes, patch identity, and candidate commit; fixed success strings do
not satisfy acceptance.

## 13. Candidate and evidence commits

Embedding the hash of the commit that contains the embedding file would be circular.
The implementation therefore uses two identities:

1. `candidate_commit`: the tested code, schema, migration, harness, and YAML source
   candidate.
2. `evidence_commit`: a descendant that adds resolved YAML/Markdown/evidence artifacts and
   changes no runtime code.

The Markdown handoff and resolved YAML identify `candidate_commit`. Acceptance receipts bind that
candidate plus YAML, Markdown, manifest, workload, and other artifact hashes. The final
report separately identifies the evidence commit at branch HEAD.

## 14. Specification artifacts

`docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml` is the machine-readable
normative source. Its serialization is the JSON-compatible subset of YAML 1.2 so the
standard-library JSON parser can validate it deterministically without installing a
new dependency. This format decision applies only to the specification artifact; it
does not create a second runtime state authority. The complete Markdown handoff is
derived from the same resolved facts and includes the spec version, YAML SHA-256,
candidate commit, evidence commit, acceptance scope, limitations, and continuation
prompt. PDF delivery and all PDF rendering/visual acceptance were cancelled by the
user before any PDF was generated.

## 15. Deprecation policy

V3 JSON stores, session registries, worker pools, transport ledgers, older
executor/runner scripts, and Windows-MCP glue are soft-deprecated only after V4 has a
compatible migration path, rollback documentation, focused tests, reference scans,
and full regression evidence. No irreversible deletion occurs before a separately
authorized production cutover and archived rollback proof.

## 16. Acceptance matrix

- AC01: concurrent compare-and-swap permits exactly one commit.
- AC02: transition idempotency, content conflict, epoch and lease fencing.
- AC03: crash recovery across transaction, dispatch, submit, response, and cleanup.
- AC04: deterministic rejection of false completion.
- AC05: two-slot dynamic Workers, persistent queue, dependency release.
- AC06: resource and normalized path boundaries.
- AC07: browser rebind and zero duplicate submit.
- AC08: Windows/ChatGPT login and verification blockers.
- AC09: review deadline and late counter-evidence retention.
- AC10: code candidate, evidence, and experimental-install identity.
- AC11: focused tests plus complete baseline regression.
- AC12: real short performance/recovery soak with two Workers, slot reuse, at least
  one scheduling recovery, zero duplicate submits, zero errors, and measured runtime
  environment. PASS does not establish 24-hour stability.

## 17. Stop conditions

Stop and fail closed on runtime-profile conflict, worktree or branch ownership
conflict, baseline drift, overlapping user modifications, invalid database PRAGMAs,
missing root contract, uncertain browser submission, authentication challenge,
unexpected production mutation, unrecoverable regression failure, or evidence that
would require weakening an acceptance condition.
