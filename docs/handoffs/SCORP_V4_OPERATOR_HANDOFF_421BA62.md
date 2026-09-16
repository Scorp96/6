# SCORP V4 Operator Handoff — 421BA62

## Current identity

- Code candidate: `421ba622784a868aa55cc71d83309b32911f7803`
- Evidence head: `32ba4a83a1050a7f03f0833371ec546e4b919704`
- Candidate manifest: `docs/handoffs/SCORP_V4_CANDIDATE_MANIFEST_421BA62.json`
- Canonical manifest SHA-256: `e7e2eefc129195f9fe3bc6787b21f9db1cf0e79b7a478c3d30076099d8ae7e89`
- Validation: `docs/handoffs/SCORP_V4_VALIDATION_421BA62.json`
- Validation file SHA-256: `4234b8129f3b9aff267eacb0a17e0935f5ec1dc0f9576e810b755f57123efbce`

## Verified

Offline regression is TEST_VERIFIED: V4 core 215/215 PASS, GUI Bridge 528/528 PASS, focused unpromoted recovery 1/1 PASS, live-ack recovery 6/6 PASS, compileall PASS.

The fresh real two-Worker Chrome Use canary passed on this exact candidate and manifest. It created two distinct ChatGPT conversations, captured two exact marker responses, recorded two `RESPONSE_CAPTURED` intents, made two submit actions, and recorded zero duplicate submits.

Live canary evidence:
`C:/ScorpAgent/v4-core-lab/canary-421ba62-20260916-1526/evidence.json`

Evidence SHA-256:
`d40352ff8d89ed3c4afe3d9b62f8e41639d22d26e14008b502d79b542de90ee4`

## Current boundary

`TEST_VERIFIED = true`

`BROWSER_TWO_WORKER_LIVE_GATE = PASS`

`LIVE_VERIFIED = false`

`ACCEPTED = false`

`DEPLOYED = false`

`PRODUCTION_CHANGED = false`

The former `CONVERSATION_URL_MISSING` blocker is closed for the exact 421BA62 live marker canary. This does not prove the whole product.

## Next acceptance gate

Use a fresh isolated project/database/driver state. Prove:

`real GPT WORK_RESULT/1`
→ `candidate_results`
→ independent verification
→ assignment `RETIRED`
→ lease `RELEASED`
→ bounded `LocalExecutionAdapter`
→ machine evidence

Do not reuse DEA5433 ambiguous intents. Do not change production.

After that, remaining gates are physical browser rebind, live restart recovery, Windows reboot without interactive login, unattended soak, and production cutover.
