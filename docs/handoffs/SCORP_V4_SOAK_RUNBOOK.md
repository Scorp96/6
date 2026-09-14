# SCORP V4 short performance and recovery soak

## Revised acceptance boundary

The user replaced the original 24-hour gate with a short real-duration performance
and recovery test for this delivery. AC12 may PASS when the short run satisfies the
contract below. That PASS is deliberately scoped and does **not** prove 24-hour or
long-duration stability.

## Pass contract

The receipt must report the requested and actual elapsed durations, task cycles,
verified task count, tasks-per-second throughput, peak concurrency, slot reuse,
scheduler recoveries, maximum recovery time, browser-intent count, submit attempts,
duplicate submissions, errors, database hash, and host environment.

Pass requires:

- actual elapsed duration is at least the requested duration;
- peak dynamic Worker concurrency is exactly two;
- every cycle verifies T1, T2, and dependency-gated T3;
- Worker slots are reused;
- at least one expired-lease scheduling recovery completes within the declared bound;
- every browser intent traverses the real V4 adapter state machine with the injected
  soak engine and a second `submit_once` call proves idempotency;
- duplicate submissions and errors remain zero;
- SQLite durability settings and runtime environment are recorded; and
- `long_duration_stability_proven` remains `false`.

The injected soak browser engine exercises the real local intent/outbox/adapter
logic; it is not live ChatGPT evidence. The real browser/login gate is reported
separately.

## Command

Run from the frozen candidate installation:

```powershell
$env:PYTHONPATH = 'C:\ScorpAgent\v4-core-lab\scorp-agent'
python -B -m master_a_dynamic_v4.soak_harness `
  --lab-root 'C:\ScorpAgent\v4-core-lab' `
  --duration-seconds 10 `
  --output 'C:\ScorpAgent\v4-core-lab\evidence\ac12-short-soak.json'
```

Use a new isolated `short-soak.sqlite3` database. Refuse to overwrite an existing
benchmark database because mixing runs would make throughput and recovery accounting
ambiguous.

## Stop conditions

Stop on duration under-run, concurrency other than two, missing slot reuse, missing
or slow recovery, task-accounting mismatch, duplicate submit, any error, incomplete
environment data, existing benchmark database, or any claim that the short run proves
long-duration stability.
