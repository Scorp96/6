# SCORP V4 Operator Handoff — 1AA021A

## Current identity

- Code candidate: `1aa021aebc6643668f40182f49da0978c099230d`
- Baseline: `4dd0bd7b5cb1c1420f9bb91835aec692daca1431`
- Branch: `fix/persistent-master-p0-fix-20260921`
- Isolated worktree: `C:/ScorpAgent/worktrees/v4-persistent-master-p0-fix-20260921`
- Candidate manifest: `docs/handoffs/SCORP_V4_CANDIDATE_MANIFEST_1AA021A.json`
- Canonical manifest SHA-256: `589b1d9694ac0871d2fca6b9a1583db279784c18f8dbc587ee81f7124c579e3b`
- Validation: `docs/handoffs/SCORP_V4_VALIDATION_1AA021A.json`

## What was fixed

1. New Master reasoning intents now persist `SCORP_REASONING::<token>` before the large prompt. After a crash, the browser adapter uses this durable short token for a read-only snapshot and never blind-resends an ambiguous submit. Older intents retain the intent-id fallback.
2. The daemon builds its local controller/browser objects before acquiring the short daemon lease, so slow Chrome Use startup cannot consume the TTL. The existing action heartbeat continues renewing the lease during long handlers.

## Offline evidence

- V4 core: **270/270 PASS**.
- GUI bridge: **567/567 PASS**.
- Candidate validation script: **PASS** (including compileall).
- Real `ChromeUseActorDriverV3` large-prompt Master recovery: **PASS**; the test observed only `get url`, `bringToFront`, and `read`, with no `open`, `fill`, `click`, `press`, or `type`.
- Daemon startup lease ordering and long-action heartbeat regressions: **PASS**.

## Acceptance boundary

`TEST_VERIFIED = true`

`LIVE_VERIFIED = false`

`ACCEPTED = false`

`DEPLOYED = false`

`PRODUCTION_CHANGED = false`

The current candidate has not yet received fresh real Windows+Chrome evidence after this fix. The previous `4dd0bd7` run stopped at a real Master reasoning intent in `MAY_HAVE_SUBMITTED/BLOCKED_AMBIGUOUS`; do not replay that intent and do not call it a pass. Use a fresh isolated state for the next canary.

## Next safe gate

Run offline validation again from this checkout, then perform a read-only preflight. If the interactive Windows session is logged in and the candidate manifest matches exactly, run one fresh current-candidate canary. If the submission result is ambiguous, preserve the intent and stop. Only after the Master recovery gate passes should the two Worker and structured `WORK_RESULT/1` gates run.
