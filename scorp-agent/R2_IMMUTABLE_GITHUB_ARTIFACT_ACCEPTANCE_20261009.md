# SCORP R2 — Real pinned Git artifact verification handoff (2026-10-09)

## Purpose and exact safety boundary

This candidate adds a **real, immutable file-content check** to the
existing GitHub work-result comment pipeline. The previous comment-only
prototype accepted a 64-character SHA256 shape without fetching the
referenced file. New code now proves that exact GitHub file bytes exist
in a specified 40-hex Git commit, match the comment's expected SHA256,
pass a Git blob identity check and describe the same project, assignment,
task, worker slot and state version. It does **not** prove that a distinct
ChatGPT Worker completed a turn, that a browser was authenticated, or that
a Master may wake or send.

The strict invariant remains:

- GitHub comment accepted != pinned Git artifact verified.
- Pinned Git artifact verified != actual GPT Worker identity attested.
- Actual work-product presence != host TURN_FINAL_CONFIRMED.
- Two verified artifact rows != browser send authorized.
- No send / wake / local-execute code is present in these modules.

## New code

1. `master_a_dynamic_v4/isolated_gh_immutable_artifact_v4.py`:
   native Windows authenticated `gh api` GET of
   `repos/Scorp96/6/contents/<allowlisted-json-path>?ref=<exact-40-hex-commit>`.
   It explicitly rejects branch refs, traversal and arbitrary paths.
   Accept only GitHub file + base64 payload <=32 KiB, exact Git blob
   SHA1 computed from bytes, SHA256 supplied independently and exact
   8-field JSON task envelope; never log native stdout/stderr.
2. `master_a_dynamic_v4/isolated_gh_verified_result_latch_v4.py`:
   read GitHub comment, check the original exact SHA256 claim, verify
   pinned real file, stage its ordinary no-send review row, then add
   a separate attestation receipt containing commit, path digest,
   content SHA256 and comment id. This is **fail closed**, not atomic
   across both tables: a crash between commits leaves an unverified
   base row. A verification barrier cannot pass without the receipt,
   and a caller must not silently replay the ambiguous transaction.
3. `master_a_dynamic_v4/isolated_two_verified_blobs_review_v4.py`:
   read-only two-slot barrier insists on exactly two assignments,
   distinct paths, expected commit/path digest, metadata SHA256 matching
   blob receipt SHA256 and original author/issue/generation. Only emits
   `BOTH_IMMUTABLE_BLOBS_FOR_HUMAN_REVIEW`. All send/wake permissions false.
4. Tests `tests/test_isolated_gh_immutable_artifact_v4.py` (+21)
   and `tests/test_isolated_gh_verified_blob_latch_v4.py` (+16).
   Windows candidate test floor **1508** = V4 678 + GUI Bridge 801
   + Broker 29.

## Real Windows receipts (no replays)

- #2444: original pinned artifact verifier source candidate
  `d06f08248f920860a87a388f55217dfab27ee64d`,
  1492/1492 Windows full offline tests, no production writes.
- #2445: **native Windows** used authenticated `gh` GET at pinned Git
  commit `d91fd4569f92c8bf2ab39aac6884ee46382cff82`;
  both synthetic Worker files present, SHA256 and Git blob
  hashes computed, no original account/token or page data exposed.
- #2446: candidate `34a60f087c0bf0681319a427d480de7b258f6af6`;
  full Windows **1508/1508** pass, V4 678, Bridge 801, Broker 29,
  zero failures/errors/skips, compile PASS, browser_send NOT_ATTEMPTED,
  production_writes NONE.
- #2447: actual Windows native GitHub REST GET on two **synthetic**
  comment IDs 6075575072/6075577361, then actual immutable Git
  file GET on same fixed commit `d91fd456...`. First review row
  + first evidence -> waiting. Second review row + second evidence ->
  `BOTH_IMMUTABLE_BLOBS_FOR_HUMAN_REVIEW`. Dedicated SQLite:
  **2 comment review rows + 2 actual blob evidence receipts**,
  quick_check PASS; actual_worker_identity_verified=false,
  host_terminal_event_attested=false, wake/send/local-exec=false.

This is a *synthetic integration experiment*. The two fake comments and
small fake JSON files are not two actual separate ChatGPT Worker runs
and do not represent user business work. They must never be used to
unlock original SCORP Master or R1 submission.

## Outstanding engineering work and estimated review rounds

The full objective is 24-hour autonomous persistent Master + two
real Workers on the user's Windows machine, with exactly-once browser
submit, safe pause/restart, and no paid model API or CAPTCHA bypass.

Assuming the current isolation architecture remains viable, estimate
**4–6 substantive additional acceptance rounds** after the pinned-blob
review proof:

1. Real work-product provenance: two independently scoped genuine
   GPT/Worker results, each bound to task, project, file, generation,
   source identity, no self-posted fake completion.
2. Master/2-Worker durable coordination: verified result consumption,
   stale epoch rejection, crash/resume, one-shot claim and no-send
   safety even after Windows process restart.
3. Trusted ChatGPT turn/submit boundary OR explicit alternate result
   protocol: reliable completion and separate authorization for the
   next message. Chrome Use tab counts and DOM Stop-button heuristics
   are not a trusted host event. This is the **highest architectural
   uncertainty** and may increase the number of rounds.
4. Isolated 24-hour local observation-and-coordination soak under
   a separately authorized schedule, with repeatable safe recovery.
5. Explicit, independently authorized production/R1 cutover and
   post-cutover rollback/stop validation. Not authorized today.

If a trusted host terminal issuer is unavailable by design, the user
must choose an artifact-driven Worker protocol instead of pretending
the ChatGPT web UI can reliably emit a host-final signal.

## Preserve original system

- SCORP original R1 has one BLOCKED_AMBIGUOUS submit. NEVER replay.
- Protected GUI bridge and watchdog must not be modified/restarted.
- Frozen 15-minute observer remains separately protected.
- Candidate code stays on Draft PR #1; never merge or install scheduler
  as a side effect of a test.
- Isolated scratch experiments only. No credentials, private pages,
  ChatGPT browser target IDs or full worker output in result comments.
