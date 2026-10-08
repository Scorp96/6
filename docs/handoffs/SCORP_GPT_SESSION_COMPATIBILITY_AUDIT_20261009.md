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

- Current Windows Agent online status: NOT_VERIFIED (latest retrieved remote
  execution receipts are from 2026-10-01).
- This web ChatGPT session has GitHub repository access, **not** automatic
  Windows shell/Named Pipe access.
- Trustworthy model metadata for a browser conversation: NOT_VERIFIED.
- Browser send / real response canary: NOT_RUN for this candidate.
- Existing repository's GPT-5.6 Sol identity policy: STILL IN EFFECT.
  Changing an evidence field is not authorization to waive it.
- Full candidate validation and real Windows execution: NOT_RUN.
- Production cutover: NOT_AUTHORIZED.

## This branch changes only

- Truthful model evidence in the V4 one-shot runtime report.
- Regression check that the report calls the unverified-evidence function.
- This engineering audit.

No local execution, browser submission, scheduled-task change, SQLite edit,
release installation, model substitution, or production cutover is performed.

## Recommended next acceptance tests

- Confirm `runtime.status`, `master.status`, `worker.status` and
  `evidence.query` via authorized read-only pipe on the current Windows host.
- Verify `reasoning_model` remains null without trustworthy host attestation.
- Verify the V4 GUI bridge identifies the *original conversation* and completion
  independently, with no speculative prompt resend.
- Verify all GPT model labels are treated as metadata; only host-verified
  session capability, root contract and permissions control admission.
