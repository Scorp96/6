# SCORP V4 Master A Dynamic Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-isolated SCORP V4 candidate with one persistent logical Master A, two dynamically allocated Workers, one authoritative SQLite transaction store, a fail-closed browser outbox, deterministic acceptance, compatible snapshot migration, and auditable YAML/Markdown evidence.

**Architecture:** Add a shadow V4 package on the exact P0 baseline. SQLite owns all semantic state and commits durable external-action intents before browser I/O; the existing Chrome-use driver is wrapped behind the V4 adapter without retaining JSON authority. Existing V3 and production paths remain unchanged and available for rollback.

**Tech Stack:** Python 3 standard library (`sqlite3`, `dataclasses`, `enum`, `hashlib`, `json`, `csv`, `decimal`, `pathlib`, `unittest`) including deterministic parsing of the JSON-compatible YAML 1.2 source, plus PowerShell for experimental installation checks.

**Spec:** `docs/superpowers/specs/2026-09-13-scorp-v4-master-a-dynamic-workers-design.md`

## Global Constraints

- Baseline is exactly `p0/chatgpt-transport-bakeoff@e87a9f70c55622e5ca742284e00918e13552f8ac`.
- Work only on `feature/v4-master-a-dynamic-workers` in `C:\ScorpAgent\worktrees\v4-transaction-core`.
- Use `C:\ScorpAgent\v4-core-lab` for experimental state only.
- Do not merge, push, deploy, switch production, modify existing scheduled tasks, or write `C:\ScorpAgent\state-v3\active` or `C:\ScorpAgent\state-v4`.
- SQLite must report `journal_mode=delete`, `synchronous=2`, `foreign_keys=1`, and `busy_timeout=5000` on every connection.
- No browser, network, GitHub, or subprocess I/O may occur inside a database transaction.
- Default Worker concurrency is exactly two; additional work remains durable and queued.
- Unknown browser submission or authentication state fails closed and never automatically resubmits.
- JSON is only `IMPORTED_SNAPSHOT` input or read-only export after V4 authority begins.
- AC12 is the user-approved short real-duration performance/recovery soak. Its PASS does not imply 24-hour stability.
- Preserve TDD RED output before adding the minimal GREEN implementation for every behavior.

---

### Task 1: Normative YAML and package contract scaffold

**Files:**
- Create: `docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml`
- Create: `scorp-agent/master_a_dynamic_v4/__init__.py`
- Create: `scorp-agent/master_a_dynamic_v4/models.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_spec_contract.py`

**Interfaces:**
- Consumes: the approved design and fixed repository identity.
- Produces: `CommitResult`, `IntentState`, `AcceptanceStatus`, canonical hashing helpers, and a parseable normative YAML structure used by every later task.

- [ ] **Step 1: Write the failing spec/model tests**

```python
def test_commit_results_are_closed_set(self):
    self.assertEqual(
        {"COMMITTED", "ALREADY_COMMITTED", "VERSION_CONFLICT", "FENCED", "REJECTED"},
        {value.value for value in CommitResult},
    )

def test_yaml_declares_exact_baseline_and_sqlite_pragmas(self):
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    self.assertEqual(EXPECTED_COMMIT, spec["repository"]["baseline_commit"])
    self.assertEqual("DELETE", spec["sqlite"]["journal_mode"])
    self.assertEqual(5000, spec["sqlite"]["busy_timeout_ms"])
```

- [ ] **Step 2: Run the tests and record RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_spec_contract -v`

Expected: import/file failure because the V4 package and YAML do not exist.

- [ ] **Step 3: Add the minimal closed enums, canonical hashing, and YAML contract**

The JSON-compatible YAML 1.2 source defines spec version, repository/worktree/lab identity, authority tables,
commit results, browser states, Worker limits, AC01-AC12, deprecation policy, stop
conditions, and `candidate_commit: PENDING_CANDIDATE_COMMIT` until the tested code
candidate exists.

- [ ] **Step 4: Run the focused tests and record GREEN**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_spec_contract -v`

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

```text
git add docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml scorp-agent/master_a_dynamic_v4
git commit -m "test: define SCORP V4 normative contract"
```

### Task 2: SQLite schema and connection invariants

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/schema.sql`
- Create: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_sqlite_schema.py`

**Interfaces:**
- Consumes: enums and canonical hashing from `models.py`.
- Produces: `StateStore(path, allowed_roots)`, `connection_settings()`, `create_contract(...)`, and all normalized tables required by the design.

- [ ] **Step 1: Write failing schema/PRAGMA tests**

```python
with StateStore(db, allowed_roots=[worktree, lab]) as store:
    self.assertEqual(
        {"journal_mode": "delete", "synchronous": 2, "foreign_keys": 1, "busy_timeout": 5000},
        store.connection_settings(),
    )
    self.assertTrue(REQUIRED_TABLES <= set(store.table_names()))
```

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_sqlite_schema -v`

Expected: failure because `StateStore` and `schema.sql` are absent.

- [ ] **Step 3: Implement schema migration and verified connections**

Use `isolation_level=None`, set all four PRAGMAs, verify returned values, execute
versioned schema in an immediate transaction, and fail with `StoreInvariantError`
when an existing database has an unsupported schema or journal mode.

- [ ] **Step 4: Run GREEN and reopen/restart checks**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_sqlite_schema -v`

Expected: all schema tests pass against fresh and reopened databases.

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4/schema.sql scorp-agent/master_a_dynamic_v4/state_store.py scorp-agent/master_a_dynamic_v4/tests/test_sqlite_schema.py
git commit -m "feat: add authoritative V4 SQLite schema"
```

### Task 3: AC01 and AC02 transactional commit, idempotency, and fencing

**Files:**
- Modify: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Create: `scorp-agent/master_a_dynamic_v4/contracts.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac01_concurrent_cas.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac02_idempotency_fencing.py`

**Interfaces:**
- Consumes: `StateStore`, `CommitResult`, canonical hashes, SQLite schema.
- Produces: `StateStore.commit(expected_version, master_epoch, transition_id, proposal, evidence_refs) -> CommitResult` and `advance_master_epoch(expected_epoch) -> int`.

- [ ] **Step 1: Write concurrent process/thread RED tests**

```python
results = race_two_commits(expected_version=0, distinct_transition_ids=True)
self.assertEqual(1, results.count(CommitResult.COMMITTED))
self.assertEqual(1, results.count(CommitResult.VERSION_CONFLICT))
```

Also assert identical transition replay returns `ALREADY_COMMITTED`, changed content
with the same transition ID returns `REJECTED`, and an old epoch returns `FENCED`.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_ac01_concurrent_cas master_a_dynamic_v4.tests.test_ac02_idempotency_fencing -v`

Expected: missing `commit` or incorrect concurrency result.

- [ ] **Step 3: Implement the minimal immediate-transaction commit algorithm**

Insert canonical transition identity before updating state, enforce unique
`transition_id`, compare content hash on replay, check epoch before version, validate
evidence references, and increment state version once with a guarded SQL update.

- [ ] **Step 4: Run GREEN repeatedly**

Run the focused command five times to catch locking races; every run must pass with
exactly one commit winner.

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4
git commit -m "feat: commit V4 state with CAS and fencing"
```

### Task 4: AC05 and AC06 task DAG, two Worker slots, leases, and path boundaries

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/scheduler.py`
- Create: `scorp-agent/master_a_dynamic_v4/path_policy.py`
- Modify: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac05_dynamic_workers.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac06_resource_boundary.py`

**Interfaces:**
- Consumes: `StateStore.commit` and normalized task/assignment/lease tables.
- Produces: `Scheduler.enqueue_graph(...)`, `Scheduler.claim_runnable(limit=2)`, `Scheduler.record_candidate(...)`, `Scheduler.retire_assignment(...)`, and `PathPolicy.authorize(paths, access_mode)`.

- [ ] **Step 1: Write Worker/DAG/path RED tests**

```python
first = scheduler.claim_runnable(limit=2)
self.assertEqual({"T1", "T2"}, {item.task_id for item in first})
self.assertEqual([], scheduler.claim_runnable(limit=2))

with self.assertRaises(PathBoundaryError):
    policy.authorize([r"C:\ScorpAgent\state-v3\active\project-state.json"], "write")
```

Tests also cover dependency blocking, conflicting write scopes, unique assignments,
reusable slots, unguessable lease tokens, expired lease fencing, `..` traversal,
symlink/reparse resolution, case-insensitive Windows paths, and paths sharing a mere
string prefix with an allowed root.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_ac05_dynamic_workers master_a_dynamic_v4.tests.test_ac06_resource_boundary -v`

- [ ] **Step 3: Implement minimal durable scheduling and canonical path policy**

Use database ordering for deterministic queue selection, reserve a slot and lease in
one transaction, and resolve absolute paths before comparing them to exact allowed
roots. Never authorize based on prompts or unverified string prefixes.

- [ ] **Step 4: Run GREEN, including restart with active and expired leases**

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4
git commit -m "feat: schedule two fenced dynamic workers"
```

### Task 5: AC03, AC07, and AC08 durable browser intent adapter

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/browser_adapter.py`
- Create: `scorp-agent/master_a_dynamic_v4/recovery.py`
- Modify: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac03_crash_recovery.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac07_browser_rebind.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac08_auth_blockers.py`

**Interfaces:**
- Consumes: SQLite `action_intents`, `outbox`, `browser_bindings`, and an injected browser engine with read/submit primitives.
- Produces: `BrowserAdapter.submit_once(intent_id)`, `BrowserAdapter.reconcile(intent_id)`, and `BrowserAdapter.rebind(channel, ...)`.

- [ ] **Step 1: Write fault-injection RED tests**

```python
adapter = BrowserAdapter(store, engine, failpoint="after_may_have_submitted")
with self.assertRaises(InjectedCrash):
    adapter.submit_once(intent_id)
self.assertEqual("MAY_HAVE_SUBMITTED", store.get_intent(intent_id).state)
self.assertEqual(0, engine.submit_count)
```

Additional tests crash after the browser reports submit and before local confirmation,
restart the adapter, reconcile one unique marker, and assert `submit_count == 1`.
Unknown, zero-proof, or multiple matches become `BLOCKED_AMBIGUOUS`. Explicit proof
of no submit is the only route to `VERIFIED_NOT_SUBMITTED`. Logged-out, locked,
CAPTCHA, challenge, or verification states persist a blocker and do not submit.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_ac03_crash_recovery master_a_dynamic_v4.tests.test_ac07_browser_rebind master_a_dynamic_v4.tests.test_ac08_auth_blockers -v`

- [ ] **Step 3: Implement transactions around, never across, browser I/O**

Commit `MAY_HAVE_SUBMITTED`, close the transaction, call the injected engine, and
open a new transaction for confirmed identity or ambiguity. Recovery enumerates
durable outbox rows and performs read-only reconciliation before any retry decision.

- [ ] **Step 4: Run GREEN and verify engine call traces contain no duplicate submit**

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4
git commit -m "feat: reconcile browser intents without duplicate submit"
```

### Task 6: AC04 and AC09 independent acceptance and durable review findings

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/acceptance.py`
- Create: `scorp-agent/master_a_dynamic_v4/evidence.py`
- Modify: `scorp-agent/master_a_dynamic_v4/state_store.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac04_false_completion.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac09_review_deadline.py`

**Interfaces:**
- Consumes: a read-only SQLite snapshot, required AC IDs, candidate identity, and evidence receipts.
- Produces: `AcceptanceValidator.evaluate(project_id, candidate_commit, artifact_hashes) -> AcceptanceDecision` and review inbox operations.

- [ ] **Step 1: Write false-completion RED tests**

Construct otherwise-valid state variants with empty evidence, fake PASS, active
Worker, unprocessed candidate, stale candidate commit, unresolved outbox,
`MAY_HAVE_SUBMITTED`, ambiguous browser state, blocking finding, and late
counter-evidence. Each must return `REJECTED` with a stable blocker code.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_ac04_false_completion master_a_dynamic_v4.tests.test_ac09_review_deadline -v`

- [ ] **Step 3: Implement deterministic acceptance and append-only findings**

The validator has read-only database access and no method that writes a PASS. A
separate state-store operation may apply an already computed decision. Late findings
retain the prior verdict, set `late=1`, and create a new blocking event.

- [ ] **Step 4: Run GREEN**

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4
git commit -m "feat: reject false V4 completion deterministically"
```

### Task 7: Imported snapshots, read-only exports, and soft deprecation

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/import_snapshot.py`
- Create: `scorp-agent/master_a_dynamic_v4/export_snapshot.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_snapshot_migration.py`
- Create: `docs/handoffs/SCORP_V4_MIGRATION_AND_ROLLBACK.md`

**Interfaces:**
- Consumes: legacy JSON bytes and `StateStore` import operations.
- Produces: `import_snapshot(path, store) -> ImportReceipt` and `export_read_only(store, project_id) -> dict`.

- [ ] **Step 1: Write migration RED tests**

Assert original bytes SHA-256 is preserved, missing root contract produces BLOCKED,
conflicts are recorded rather than overwritten, no prehistory is fabricated, exports
carry `authority: READ_ONLY_EXPORT`, and V4 never changes the source JSON.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_snapshot_migration -v`

- [ ] **Step 3: Implement the importer/exporter and migration/rollback guide**

Import in one transaction with an immutable receipt. Export from a read-only
connection. The guide names every V3 JSON store and explains that it remains rollback
material, not V4 authority.

- [ ] **Step 4: Run GREEN and hash source fixtures before/after**

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4 docs/handoffs/SCORP_V4_MIGRATION_AND_ROLLBACK.md
git commit -m "feat: import legacy snapshots without dual authority"
```

### Task 8: Real T1/T2/T3 CSV Git workload

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/csv_workload/__init__.py`
- Create: `scorp-agent/master_a_dynamic_v4/csv_workload/reader.py`
- Create: `scorp-agent/master_a_dynamic_v4/csv_workload/aggregate.py`
- Create: `scorp-agent/master_a_dynamic_v4/csv_workload/cli.py`
- Create: `scorp-agent/master_a_dynamic_v4/csv_workload/graph.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_csv_workload.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/fixtures/orders.csv`

**Interfaces:**
- Consumes: Scheduler graph and Python `Decimal`.
- Produces: `read_rows(path)`, `aggregate_by_category(rows)`, `stable_report(path)`, and `build_task_graph()`.

- [ ] **Step 1: Write CSV behavior RED tests**

```python
self.assertEqual(Decimal("12.30"), rows[0].amount)
self.assertEqual(["alpha", "beta"], list(aggregate_by_category(rows)))
self.assertEqual(expected_json + "\n", run_cli(fixture))
```

Test malformed headers, malformed decimals, empty input, deterministic Unicode and
key ordering, T1/T2 disjoint concurrent admission, and T3 dependency blocking.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_csv_workload -v`

- [ ] **Step 3: Implement T1, T2, T3 and graph integration**

Parse the entire file before returning rows, aggregate only with `Decimal`, and emit
canonical JSON with an explicit newline. Store real output hashes and task results.

- [ ] **Step 4: Run GREEN and execute the CLI as a subprocess outside transactions**

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4
git commit -m "feat: run real two-worker CSV workload"
```

### Task 9: AC10 candidate and experimental-install identity

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/install_manifest.py`
- Create: `scorp-agent/master_a_dynamic_v4/install-lab.ps1`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac10_release_identity.py`
- Create: `docs/handoffs/SCORP_V4_EXPERIMENTAL_INSTALL.md`

**Interfaces:**
- Consumes: candidate commit, tracked file hashes, lab root, and current production manifests.
- Produces: a deterministic candidate manifest, lab installation receipt, and before/after protected-path manifest.

- [ ] **Step 1: Write identity RED tests**

Assert candidate commit mismatch, changed file hash, changed install manifest, path
escape, and protected-path mutation all fail. Assert lab install copies only allowlisted
files and records source commit, source tree, interpreter, database, and manifest hash.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_ac10_release_identity -v`

- [ ] **Step 3: Implement deterministic manifest and guarded lab installer**

The installer defaults to `C:\ScorpAgent\v4-core-lab`, refuses any target under
protected production roots, snapshots protected files read-only before and after,
and never registers or changes a scheduled task.

- [ ] **Step 4: Run GREEN**

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4 docs/handoffs/SCORP_V4_EXPERIMENTAL_INSTALL.md
git commit -m "feat: verify V4 candidate and lab installation identity"
```

### Task 10: AC12 short performance and recovery soak

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/soak_harness.py`
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac12_soak_contract.py`
- Create: `docs/handoffs/SCORP_V4_SOAK_RUNBOOK.md`

**Interfaces:**
- Consumes: SQLite store, browser adapter, scheduler, and acceptance evidence model.
- Produces: `validate_short_soak_receipt(receipt)`, a real-duration benchmark, and a machine-readable receipt.

- [ ] **Step 1: Write soak truthfulness RED tests**

Reject duration under-run, concurrency other than two, missing slot reuse, missing
scheduler recovery, task-accounting mismatch, duplicate submit, errors, incomplete
environment evidence, or a false long-duration-stability claim.

- [ ] **Step 2: Run RED**

Run: `python -B -m unittest master_a_dynamic_v4.tests.test_ac12_soak_contract -v`

- [ ] **Step 3: Implement the short real-duration benchmark and runbook**

Run repeated T1/T2/T3 cycles through the real scheduler, expire and recover a lease,
reuse Worker slots, exercise the browser adapter with an injected soak engine, and
measure duration, throughput, recovery, duplicates, errors, database hash, and host.

- [ ] **Step 4: Run GREEN and execute the user-approved short soak**

- [ ] **Step 5: Commit**

```text
git add scorp-agent/master_a_dynamic_v4 docs/handoffs/SCORP_V4_SOAK_RUNBOOK.md
git commit -m "feat: add truthful V4 unattended soak harness"
```

### Task 11: AC11 full regression, references, and deprecation evidence

**Files:**
- Create: `scorp-agent/master_a_dynamic_v4/tests/test_ac11_regression_contract.py`
- Create: `docs/handoffs/SCORP_V4_DEPRECATION_INVENTORY.md`
- Create: `docs/handoffs/SCORP_V4_VERIFICATION.md`

**Interfaces:**
- Consumes: all prior tests and Git/reference scans.
- Produces: exact test counts, command receipts, reference inventory, and a soft-deprecation/no-deletion decision.

- [ ] **Step 1: Write regression-contract RED test**

The test reads a machine-generated verification receipt and rejects missing commands,
nonzero exits, omitted suites, or a claim that the short soak proves long stability.

- [ ] **Step 2: Run all V4 tests**

Run: `python -B -m unittest discover -s scorp-agent/master_a_dynamic_v4/tests -v`

- [ ] **Step 3: Run the fixed-baseline bridge regression from the candidate tree**

Run: `python -B -m unittest discover -s scorp-agent/chatgpt-gui-bridge/tests -v`

Expected baseline reference: 397 tests, zero failures. Record the fresh actual count;
do not force it to equal the reference if the suite legitimately changes.

- [ ] **Step 4: Run PowerShell and reference scans**

Run the existing repository PowerShell acceptance scripts that do not mutate
production, compile every new Python module, run `git diff --check`, and scan all
references to V3 JSON stores, executor/runner scripts, browser drivers, installers,
state roots, and scheduled-task names. Record why each old path is retained,
soft-deprecated, migrated, or blocked from deletion.

- [ ] **Step 5: Commit regression documentation**

```text
git add scorp-agent/master_a_dynamic_v4/tests/test_ac11_regression_contract.py docs/handoffs/SCORP_V4_DEPRECATION_INVENTORY.md docs/handoffs/SCORP_V4_VERIFICATION.md
git commit -m "test: verify V4 regression and deprecation boundary"
```

### Task 12: Candidate commit, lab installation, real browser gate, and YAML/Markdown evidence

**Files:**
- Modify: `docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml`
- Create: `docs/handoffs/SCORP_V4_HANDOFF.md`
- Create: `docs/handoffs/SCORP_V4_ACCEPTANCE_STATUS.json`
- Create: `docs/handoffs/SCORP_V4_PROTECTED_PATH_MANIFEST.json`

**Interfaces:**
- Consumes: the tested code candidate commit, YAML spec, lab installer, browser adapter, and all AC receipts.
- Produces: resolved YAML, complete Markdown handoff, per-AC status, protected-path zero-change evidence, and final evidence commit. PDF is `CANCELLED_BY_USER`.

- [ ] **Step 1: Freeze and identify the code candidate**

Require a clean tree, run fresh V4 and full bridge regressions, then create a code
candidate commit. Record that hash without amending it afterward.

- [ ] **Step 2: Run the guarded experimental lab installation**

Install only to `C:\ScorpAgent\v4-core-lab`, verify candidate/manifest/database
identity, and compare protected production paths before/after. Abort on any change.

- [ ] **Step 3: Evaluate the real browser/login gate**

Read current Windows interactive and ChatGPT login/challenge state. If logged out,
locked, challenged, or uncertain, persist AC08 `BLOCKED` and perform no submit. If
healthy, run one bounded uniquely marked intent through submit/reconcile and prove one
submit, correct canonical conversation, captured response, and no duplicate attempt.

- [ ] **Step 4: Resolve YAML and create the Markdown handoff**

Replace `PENDING_CANDIDATE_COMMIT` with the frozen code candidate. Parse the YAML,
compute its SHA-256, and write the complete handoff with goal, architecture, changes,
deprecations, results, AC status, limits, paths, hashes, and continuation prompt.

- [ ] **Step 5: Verify YAML and Markdown semantics**

Parse YAML again and require the version, candidate commit, AC12 short-soak contract,
PDF cancellation marker, and all mandatory Markdown headings and hashes.

- [ ] **Step 6: Run the final acceptance validator**

AC01-AC11 use current receipts. AC12 may PASS for the short-soak scope when its real
receipt validates. Production cutover remains separately unauthorized, and the short
run must not be described as 24-hour evidence.

- [ ] **Step 7: Create the evidence commit**

```text
git add docs/handoffs/SCORP_V4_MASTER_A_DYNAMIC_WORKERS.yaml docs/handoffs/SCORP_V4_HANDOFF.md docs/handoffs/SCORP_V4_ACCEPTANCE_STATUS.json docs/handoffs/SCORP_V4_PROTECTED_PATH_MANIFEST.json
git commit -m "docs: publish SCORP V4 candidate evidence"
```

- [ ] **Step 8: Fresh final verification and report**

Re-run all V4 tests, the complete bridge regression, compile checks, YAML parse,
Markdown content checks, protected-path comparison, Git status/diff checks, and the
installed lab identity check. Report exact commands, counts, hashes, candidate commit,
evidence commit, worktree, branch, AC01-AC12, and remaining blockers. Do not push,
merge, deploy, or switch production.
