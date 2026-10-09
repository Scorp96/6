# R2 two genuine GPT Worker sessions — actionable handoff

**Status: WORKER SESSIONS NOT ATTESTED / NO BROWSER SEND / isolated Draft branch.**

## Why an additional gate is needed

On exact commit `93ae6f6b7115c22e4d7e0d8bd64775a19ccd2e40`, Windows #2447 proves two pinned Git file blobs and matching GitHub comments, but both files have exactly the 8-field metadata-only v1 envelope. That is file authenticity, **not work accomplishment** or independent GPT identity.

The new `scorp.r2.immutable-work-artifact/2` variant adds mandatory real `work_product` fields (`title`, `deliverable_markdown` >=300 characters, `review_checks` and `source_refs`). The verifier hashes the full JSON bytes at the exact commit plus separately hashes the deliverable text. A strict `require_substantive_work_product=True` prevents v1 metadata-only files from satisfying a substantive-output requirement. The optional original v1 review remains compatible for historical evidence.

The isolated receipt table `r2_substantive_work_receipts` stores only `comment_id` and SHA-256 of deliverable text (not content). The strict two-worker reviewer can demand two such receipts and returns `BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW`. **Nothing here verifies model/session provenance or authorizes a send/wake.** A long, low-quality, plagiarized or invented text can still pass syntactic checks; human technical review and provenance checks remain necessary.

## Independent session assignments (NOT yet executed)

1. Slot 1: `scorp-agent/r2-work-assignments/real-worker-session-one.json`: exact physical Master-to-Worker identity, ambiguous intent quarantine, safe binding. Expected output `scorp-agent/r2-work-artifacts/real-worker-session-one.json`.
2. Slot 2: `scorp-agent/r2-work-assignments/real-worker-session-two.json`: independently audit comment → blob two-stage crash window; recommend one-shot recovery. Expected output `scorp-agent/r2-work-artifacts/real-worker-session-two.json`.

**These are separate assignments for two genuinely separate ChatGPT sessions, not two tool calls or two response sections inside the same model invocation.** Current GitHub tools cannot create two independently authenticated ChatGPT browser workers. Another actual GPT session must open and complete the corresponding task under its own authorization. Do not issue browser messages in the original Master, the isolated one-shot ChatGPT home canary, or an unknown tab.

A future operator should bind each session to a distinct, host-observed physical session ID, generation and immutable assignment fingerprint **outside GitHub issue plaintext**, then compare signed host evidence with the artifact. Shared GitHub numeric user IDs and self-written “I am worker 2” messages are NOT identity proof. If host attestation remains unavailable, results can only be held `FOR_HUMAN_REVIEW`, not used to unlock autonomous GPT resubmission.

## Each genuine Worker must follow this exact pattern

- Read the assigned JSON from its exact SHA-pinned Git commit and research the listed specific source paths, with a separate conversation context. Write a meaningful review containing concrete code/evidence references, findings, counterexamples and human-review checks.
- Save to the unique v2 artifact JSON path through an authorized GitHub commit, do not overwrite the other's path or original frozen observer.
- Verify full Git blob SHA-1 and JSON SHA-256 from **exact pinned immutable commit**; record the independent assignment digest and exact issue/author scope.
- Publish one single-line `SCORP_R2_WORK_ARTIFACT::` comment under its explicitly designated review Issue after the file is committed, using the existing 9-field comment envelope. Do not create a `[SCORP_EXEC]` Issue to impersonate browser completion.
- Windows local native `gh` fetches the **real comment and real pinned bytes**, validates each receipt and persists the v2 text hash. First complete Worker must leave barrier waiting; only two valid scoped deliverables may reach `BOTH_SUBSTANTIVE_WORK_PRODUCTS_FOR_HUMAN_REVIEW`.
- **Even then** independent GPT session provenance and terminal status are not established by file contents. Do not infer TURN_FINAL_CONFIRMED or send authorization. No Master/Worker browser activity unless a separate browser transport, real host event issuer and user authorization pass their own gates.

## Existing non-Worker real example

`scorp-agent/r2-work-artifacts/r1-restart-recovery-review-v2.json` is a **substantive engineering note authored by the current single GPT conversation**. It reviews Windows #2317/#2318/#2322 with actual findings, confidence limits and a recovery checklist. It is **NOT assigned to one of the two independent Worker session tasks**, and is explicitly marked non-attested. It is a non-synthetic content proof and potential starting reference, not a successful Worker identity canary.

## Acceptance matrix

| Gate | Pass means | Current |
| --- | --- | --- |
| Pinned Git file bytes / SHA | Artifact physically exists, digest matches | v1 synthetic verified #2447 |
| v2 deliverable presence | At least 300 chars and structured review checks | Isolated candidate unit tests only until native v2 evidence arrives |
| Quality and relevance | Reviewer confirms concrete original work | Requires human/independent review |
| Distinct Worker sessions | Real host has proved two different authorized GPT sessions | **NOT_RUN** |
| Browser terminal provenance | Trusted issuer for exact intent+turn+session | **NOT_IMPLEMENTED** |
| No duplicate event | Durable idempotency & crash fencing | Partially tested, R1 untouched |
| Master auto-wake/auto-send | Explicit authorized transport and safe edges | **BLOCKED** |
| 24h acceptance | Real duration soak | **NOT_RUN** |

## Security

No raw prompt, cookies, secrets, login tokens, private browsing URLs, personal data, native `gh` raw stderr, or local target IDs in GitHub tasks or outputs. Original R1 `BLOCKED_AMBIGUOUS` stays blocked; frozen observer SHA `8da5085f906b4aeeece6fb0ae1f488bc597507b1` remains intact. New v2 tests are isolated, no production changes or ChatGPT sends.

**Next actionable step:** verify this branch's exact-head Windows CI; fetch the v2 real audit file from pinned commit and verify its genuine bytes. Then assign the *two independent sessions* (not this current session twice), and do a first real Worker result + first waiting barrier before attempting a second submission. A separate paid API and CAPTCHA bypass are not part of this work.
