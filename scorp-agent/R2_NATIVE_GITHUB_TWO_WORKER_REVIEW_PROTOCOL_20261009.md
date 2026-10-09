# SCORP R2 — Native GitHub Work Artifact Transport and Two-Worker Review Contract

**Status:** isolated candidate. No production cutover. All examples are
synthetic; no claim that any ChatGPT Worker has finished.

## Why this exists

The Windows-local Chrome Use HTTP surfaces `status`, `tabs`,
`sessions` are useful and cheap to observe but do NOT contain a
host-issued `TURN_FINAL_CONFIRMED`. A stable tab count or absent Stop button
cannot authorize a browser send or automatic GPT wake.

For deterministic tasks that explicitly publish their results, a separate
**GitHub work artifact** can be checked without opening ChatGPT or consuming
LLM inference tokens. The artifact is evidence of a GitHub submission,
not evidence of a ChatGPT UI turn boundary.

## Native Windows transport

The existing Windows host already had a working `gh` CLI session, verified
without printing credentials in SCORP #2436. The new isolated module
`master_a_dynamic_v4/isolated_gh_work_artifact_transport_v4.py`
executes a literal `gh api --hostname github.com` read-only request for one
pinned comment ID in `Scorp96/scorp-control-plane`. Output and stderr
are captured in bounded temporary files, with timeout; raw output never
enters the review status or SCORP host logs.

Source-comment fields required from GitHub **REST**:
`id`, `url` (exact REST comment link), `html_url` (exact Issue fragment),
`issue_url` (exact expected Issue), `user.id`, `created_at`,
`updated_at`, and `body`. Edited comments and mismatched IDs/issue
URLs are refused. Account display names and usernames are not used as trust
credentials.

**Not in scope:** reauthentication, copying tokens, changing permissions,
writing GitHub comments on the observer, web browser interaction, ChatGPT
message sends, or connecting to other arbitrary GitHub repos.

## Work artifact envelope

One bounded GitHub comment body containing only the exact prefix
`SCORP_R2_WORK_ARTIFACT::` and JSON:

```json
{
  "protocol": "scorp.github-work-artifact/1",
  "project_id": "project-example",
  "assignment_id": "assignment-example-one",
  "task_id": "task-example-one",
  "worker_slot": "worker-slot-1",
  "assignment_sha256": "<64 lowercase hex characters>",
  "artifact_sha256": "<64 lowercase hex characters>",
  "state_version": 0,
  "event": "ARTIFACT_STAGED_FOR_REVIEW"
}
```

The `artifact_sha256` is a digest; the actual document/body of work
must be retrieved and reviewed separately. There is no `send`, `wake`,
`master_final`, `TURN_FINAL_CONFIRMED`, or local-execute field.
Adding unexpected fields fails validation.

Do **not** paste secrets, customer information, private URLs, browser
tokens, model thinking traces, or full work results into these comments.

## Exactly-once local staging

`isolated_github_work_result_inbox_v4.py` checks caller-pinned
`project_id`, assignment, task, slot, SHA, state generation, original
Issue number and allowlisted numeric GitHub account ID; then serializes
an SQLite claim with `BEGIN IMMEDIATE`. It stores only the comment's
numeric ID, numeric author/issue ID, worker slot, fixed state, SHA
digests and generation. A second event for the same assignment or a
replayed comment cannot stage again. SQLite must be in a dedicated,
preexisting `C:\ScorpAgent\experiments\r2-work-artifact-review-*`
directory with a single-link, nonsymlink file.

`isolated_two_worker_artifact_review_v4.py` reads this SQLite in
`mode=ro` and checks both assignments and states for one project:
`AWAITING_SECOND_WORKER` after one, then
`BOTH_ARTIFACTS_FOR_REVIEW` after two exact results. It is a
**human-review convergence signal**, not dispatch authorization.

All results expose:
`browser_send_authorized=false`,
`wake_master_authorized=false`,
`wake_worker_authorized=false`,
`host_terminal_event_attested=false`,
and `local_execution_authorized=false`.

## Evidence tiers

1. **Synthetic metadata fixture**: unit tests of anti-replay and
   mixed-author/issue/spoof resistance; no real model action.
2. **Real GitHub API, connector-assisted**: issue #2432 includes two
   synthetic result comments; comments were externally verified via
   GitHub REST and their metadata supplied to isolated Windows #2435.
   Windows returned first-result, waiting, second-result, both-ready,
   replay-blocked and two durable rows. This does **not** prove Windows
   fetched GitHub independently.
3. **Native `gh` auth status and private GET**: Windows #2436 independently
   confirmed `gh` present, already authenticated, and able to GET the
   pinned private synthetic comment without printing any raw data.
4. **Native end-to-end staging with `gh` — VERIFIED FOR SYNTHETIC WORK**:
   SCORP #2438 successfully ran the checked-in Windows native reader against
   two actual GitHub REST comments #6075069271 and #6075112964, independently
   fetched BY THE WINDOWS HOST (not by ChatGPT connector forwarding). After
   slot 1: AWAITING_SECOND_WORKER. After slot 2:
   BOTH_ARTIFACTS_FOR_REVIEW. Repeated comment: BLOCKED. Exactly two
   SQLite rows, quick_check PASS, no model calls, GPT sends or production
   writes. **These were synthetic comments posted as an integration fixture,
   not genuine Worker output, real GPT terminal events, or authorization
   to automatically wake anyone.**
5. **Real Worker result**: NOT YET TESTED. Must ensure the worker actually
   authors the result from its own assigned session and posts **after**
   its task artifact has been durably completed. GitHub author identity
   alone cannot separate two models logged in to the same GitHub user.
6. **True ChatGPT terminal event**: STILL NOT IMPLEMENTED. A GitHub result
   submission may be used as an explicit *task artifact* milestone,
   but it must not be misrepresented as an independently attested
   ChatGPT UI final event.

## Original Master is quarantined

- One original R1 `BLOCKED_AMBIGUOUS` browser submit must **never** replay.
- GUI bridge `MASTER_CONVERSATION_ROTATION_REQUIRED` is still an ERROR.
- Original frozen 15m observer remains at its pinned SHA and is protected.
- No changes to production `state-v3/active`, production Task Scheduler,
  legacy GUI bridge, watchdog, or original ChatGPT browser session.
- NO automatic Master/Worker browser send or 24h unattended operation
  is enabled by any of the new work-result modules.

## Next engineering acceptance

- Build a pinned isolated reader that checks two work artifacts using
  native `gh` and a strict no-send review queue; evidence of successful
  host task completion via Windows `SCORP_EXEC_RESULT`.
- Prove crash-before/after SQLite claim, concurrent pollers,
  edited comments, swapped Issue/author, stale generation, and
  missing private GH auth all fail closed.
- Choose a *separately trusted* exact-once browser or Work task submit
  transport and real final event issuer if unattended GPT sessions
  remain a product requirement. Do not use LLM text or a local tab
  count as this issuer.
- Conduct an independently authorized isolated 24-hour real soak
  before any production cutover.

**Safety invariant:** WORK_ARTIFACT_RECEIVED != GPT_TURN_FINAL_CONFIRMED
!= BROWSER_SEND_AUTHORIZED.

## Source regression and failure transparency

- Draft PR #1 first native reader candidate at SHA
  `b2ca84bc77a859df20b5a41ec84a9526f63f16cb` DID NOT PASS:
  Windows #2437 returned 6 test failures and 1 test error. Root cause:
  one new test helper constructed a tuple containing a reader callback
  instead of a callable; one incomplete REST response test expected a
  narrower error reason than the fail-closed implementation returned.
- Corrected SHA `52efe5420007fc39d0a1bde83bf6f4b7304dc7ce`
  passed Windows #2439 exact-head **1471/1471**, V4 641,
  GUI Bridge 801, Broker 29, 0 failures/errors/skips,
  browser_send NOT_ATTEMPTED and production_writes NONE.
- Running native `gh` needs existing local GitHub login; the code
  neither collects a Token nor performs login. A subsequent run on
  a different Windows machine without this authorization must fail closed.
- No scheduled autonomous GitHub issue polling job was installed or
  enabled by these tests. The previous 15m original frozen observer task
  must remain untouched.
