# SCORP GPT session compatibility audit — 2026-10-09

Status: ISOLATED_CANDIDATE / NOT_PRODUCTION / LIVE_BROWSER_NOT_RUN

## User goal

Support GPT conversation sessions 1..N as interchangeable participants in an
existing SCORP project, without requiring the same named model for every turn.
This is not a request to exceed the proven V4 concurrent Worker capacity of 2.

## Evidence inspected

- This candidate branches from `0f1db544ee2afda86f0b4b24ba946e5edeec5b11`
  (`fix/scorp-v4-r2-runtime-keepalive-20261001`), not from the older
  `main` branch.
- `master_a_dynamic_v4/master_controller.py` has no model inference and
  uses logical `master_identity=A`.
- `master_a_dynamic_v4/activation_arbiter.py` already includes
  `HEARTBEAT_IDLE`, `RESUME_MASTER`, `WAKE_MASTER`,
  `RECONCILE_AMBIGUOUS`, and `TERMINAL` actions.
- `master_a_dynamic_v4/master_watchdog.py` is SQLite-backed; it does not
  itself open a browser or send prompts.
- V3's `WorkerConversationPoolV3` allows 1..8 configured slots; V4's
  runtime and status endpoints currently use a 2-Worker limit.
- The V4 browser engine requires structured response capture; ambiguous
  browser effects are not an invitation to resend blindly.
- The V4 CLI summary previously reported a hard-coded `GPT-5.6 Sol`
  `reasoning_model` without proving the model selected in the browser.
  This candidate changes that unverified evidence to `null` and marks
  the verification status `UNVERIFIED`.

## Distinguish identities

1. Logical roles: one Master A, zero or more scheduled Worker assignments.
2. Physical sessions: authenticated browser channels bound to exact conversation
   identities with leases, epochs and unique intents.
3. Model metadata: the model label for a conversation, *if* obtained from a
   trustworthy host-side attestation. Browser login, subscriptions, and
   assistant-generated text do not attest the model.
4. Authority: root contract, operator generation, physical binding, and
   exact browser send/acceptance gates. Model labels never grant authority.

## Migration sequence (not implemented in this change)

1. Verify current local runtime and active control queue. Do not infer the
   queue from historical docs: earlier execution evidence used the private
   control plane, while later AGENTS.md names `Scorp96/666`.
2. Reconcile isolated local R2 candidates newer than the remote branch.
   No assumption that `0f1db54` is the newest on the Windows machine.
3. Define a versioned, host-attested SessionCapability record. Unknown
   model identity must remain unknown rather than being guessed.
4. Register GPT conversations as logical Worker candidates independently of
   any model name; allocate at most two active slots until separate capacity
   and browser-load testing has passed.
5. Reuse existing ActivationArbiter and watchdog; add only missing verified
   physical-session end-of-turn observation and operator-authorized continuation.
6. Test zero duplicate browser sends through crash/restart, token/rate limits,
   unknown auth state, ambiguous side effects, and completion.
7. Require isolated 24-hour canary and explicit operator approval before
   production changes or widening the Worker limit.

## Blockers / release gates

- Current Windows Agent online status: VERIFIED for isolated read-only health and
  diagnostic tasks on 2026-10-09: [#2246](https://github.com/Scorp96/scorp-control-plane/issues/2246),
  [#2247](https://github.com/Scorp96/scorp-control-plane/issues/2247).
- This web ChatGPT session has GitHub repository access, **not** automatic
  Windows shell/Named Pipe access.
- Trustworthy model metadata for a browser conversation: NOT_VERIFIED.
- Browser send / real response canary: NOT_RUN for this candidate.
- Existing repository's GPT-5.6 Sol identity policy: STILL IN EFFECT.
  Changing an evidence field is not authorization to waive it.
- Full candidate OFFLINE validation: PASS on isolated GitHub Windows runner,
  [run 37811808599](https://github.com/Scorp96/6/actions/runs/37811808599).
  377 V4 tests, 623 GUI Bridge tests, 29 broker tests, Python compile PASS.
  Real Windows execution was read-only diagnostic only, never browser send.
- Production cutover: NOT_AUTHORIZED.

## This branch changes only

- Truthful model evidence in the V4 one-shot runtime report.
- Regression check that the report calls the unverified-evidence function.
- Pure, model-independent `session_admission`, `continuation_gate`, and
  `session_selection` modules, with dedicated offline tests.
- Cross-host Python test portability fixes and isolated GitHub Actions jobs.
- This engineering audit and a legacy ambiguous-submit safety regression.

The only real Windows execution in this phase was user-authorized, read-only
health and sanitized diagnostic queries; these did not send a browser message
or write production SQLite. The candidate branch is not installed on Windows.
No scheduled-task change, release installation, model substitution, or
production cutover was performed.

## Recommended next acceptance tests

- Confirm `runtime.status`, `master.status`, `worker.status` and
  `evidence.query` via authorized read-only pipe on the current Windows host.
- Verify `reasoning_model` remains null without trustworthy host attestation.
- Verify the V4 GUI bridge identifies the *original conversation* and completion
  independently, with no speculative prompt resend.
- Verify all GPT model labels are treated as metadata; only host-verified
  session capability, root contract and permissions control admission.

## Fresh 2026-10-09 read-only Windows facts (more recent than prior handoffs)

- [#2246](https://github.com/Scorp96/scorp-control-plane/issues/2246):
  GitHub -> Windows Agent `health` acknowledged and returned success.
- [#2247](https://github.com/Scorp96/scorp-control-plane/issues/2247):
  ScorpComputerAgent Running; R1 V4 Persistent Runtime Running;
  `runtime-v4/active/daemon-health.json` HEALTHY with recent heartbeat.
  GUI Bridge Scheduled Task Ready, not Running; its Watchdog remains Disabled.
- [#2248](https://github.com/Scorp96/scorp-control-plane/issues/2248):
  authoritative R1 project `scorp-v4-production` ACTIVE / BOOTSTRAP;
  operator RUNNING; master sessions 3 STALE; zero task nodes, Worker
  leases, or browser bindings; one action intent.
- [#2249](https://github.com/Scorp96/scorp-control-plane/issues/2249):
  the only R1 action intent is `MASTER_REASONING` /
  `BLOCKED_AMBIGUOUS`. Twenty-six R2-named directories existed;
  those with a readable database were test canaries, not a proven live R2
  replacement. An old canary HEALTHY file is historical, not live.
- [#2250](https://github.com/Scorp96/scorp-control-plane/issues/2250):
  ambiguous R1 intent created 2026-09-24; attempt=1; reason
  `CONVERSATION_URL_INVALID`. URL, remote identity, response are absent.
- [#2251](https://github.com/Scorp96/scorp-control-plane/issues/2251):
  driver metadata matches that exact turn; `browser_io_started=true`,
  `submit_edge_crossed=null`; session exists but no promoted conversation.
  Treat null as UNKNOWN, not false. No resubmission is permitted without
  independent evidence satisfying the existing fail-closed reconciliation.

## Current decision

- DO NOT RE-SEND the ambiguous Master intent.
- DO NOT reactivate or install another Master into the live R1 authority.
- DO NOT enable the currently disabled GUI Bridge Watchdog by default.
- Model-neutral admission and the 1..N *candidate session* shortlist have passed
  offline tests, but physical authority and send permissions remain separate.
- Safest next intervention: validate a *new isolated browser canary* with fresh
  unique intent and explicit operator authorization after checking that no
  existing authoritative session will be affected. The legacy production
  ambiguous intent remains BLOCKED pending genuine remote evidence or
  a separately approved operator resolution.

## 2026-10-09 continuation: GUI failure root cause and safe next seam

### Fresh read-only local evidence

- [#2253](https://github.com/Scorp96/scorp-control-plane/issues/2253):
  bridge worker process count=0, GUI health status ERROR, stale heartbeat
  since 2026-10-07, task Ready but not running; GUI Watchdog disabled.
- [#2254](https://github.com/Scorp96/scorp-control-plane/issues/2254):
  expected bridge_worker.py / chrome-use.exe / Python executables are
  present; no bridge-worker.lock. The fault is not missing executables.
- [#2256](https://github.com/Scorp96/scorp-control-plane/issues/2256):
  sanitized error enum is `MASTER_CONVERSATION_ROTATION_REQUIRED`.
  Per `session_registry_v3.py`, this triggers when a newly returned
  Master conversation URL disagrees with the existing canonical Master
  binding and no explicit, verified rotation has taken place.
- [#2257](https://github.com/Scorp96/scorp-control-plane/issues/2257):
  existing V3 Master and two registered session URLs share the same hashed
  canonical URL; driver has six conversation bindings. The *existing
  registry* is internally consistent, which does not prove that the
  browser reply's *new* URL was legitimate.
- [#2258](https://github.com/Scorp96/scorp-control-plane/issues/2258):
  V3 relay ledger contains 322 cumulative rows, including 213 GUI_AMBIGUOUS,
  104 SUPERSEDED, 2 ROUTED, 2 GUI_TIMED_OUT and 1 GUI_SUBMITTED.
- [#2259](https://github.com/Scorp96/scorp-control-plane/issues/2259):
  212/213 GUI_AMBIGUOUS are Master rows; one is a Worker.
  All 213 ambiguous rows lack durable conversation URLs and submitted_at
  timestamps. These are HISTORICAL rows, not 213 active executions.

### New isolated code, no browser sends

- `turn_completion_evidence.py`: accepts two timestamp-separated samples
  with the same physical session, canonical URL, generation, intent and
  structured-response hash. Requires affirmative host-verified final event
  at both observations. An absent Stop control does NOT prove completion.
- `physical_progress_probe.py`: calls only
  `driver.observe_current_binding(channel)` (the existing no-navigation,
  no-send driver method). A Stop control proves only GENERATING; an
  idle-looking screen is UNKNOWN, never `IDLE_CONFIRMED`.
- `verified_continuation.py`: joins host completion evidence with the
  original Arbiter continuation candidate. Rejects stale/future samples,
  respects operator/production constraints, and prioritizes STOP and
  OBSERVE_ONLY without browser work.
- `session_admission.py`: now requires `auth_verification=HOST_VERIFIED`
  and `physical_verification=HOST_VERIFIED` in addition to status strings.
  A model's statement and "ChatGPT Plus" page text are not authentication
  or physical-binding attestations.
- `gui_preflight_audit.py`: portable, read-only, redacted CLI for V3
  registry, ledger, GUI error and R1 blocked-intent metadata. No raw URL,
  turn ID, task content, token, or message text is included in the output.
- Candidate GitHub Actions gained branch-specific concurrency to cancel
  superseded runs rather than waste Windows CI minutes.

### Strong limitations

The current physical Chrome driver does **NOT** expose a trustworthy
`TURN_FINAL_CONFIRMED` event for an arbitrary original GPT conversation.
The new modules therefore remain no-send policy and diagnostic code.
Mock tests using `HOST_VERIFIED` are **not evidence** that the actual
browser transport can attest that fact. Browser/host integration and
authorization are separate blockers.

Do not replace, clear, rotate or replay the old V3 Master conversation URL.
Do not treat any historical `GUI_AMBIGUOUS` row as safe to resubmit.
Do not re-enable the GUI watchdog or turn on a live reactivation schedule
until original-thread identity, real browser turn-finalization, auth,
operator permissions and no-duplicate guarantees are tested independently.

### Validated release posture

ISOLATED CODE ONLY / WINDOWS READ-ONLY DIAGNOSTICS / NO MASTER CUTOVER /
NO LIVE BROWSER SEND / NO REPLAY / NO PRODUCTION SQLITE WRITE.

Run `scripts/run-candidate-validation.ps1` in the isolated candidate
and check the latest branch-specific GitHub Actions run before reviewing.
A full offline pass is necessary but insufficient for production.
