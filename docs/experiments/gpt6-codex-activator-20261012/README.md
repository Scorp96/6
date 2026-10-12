# GPT-6 executor / Codex activator-recorder (experimental)

**Status:** offline transaction core, NOT production, NOT a real browser GPT wake-up.

## Responsibility boundary

- GPT-6 does research, tool execution, architecture, source edits, decisions, acceptance.
- Codex only activates bound GPT requests through a future separately reviewed browser/host adapter, executes fixed verification commands as an operator, and records immutable evidence. It must not become a substitute reasoning master.
- SQLite owns request identity, activation lease, submit ambiguity and append-only hash-chained receipt records.
- Neither Codex Goals nor Codex App Server can automatically create a turn in the same ChatGPT web conversation by themselves.
- Browser activation remains disabled. No CAPTCHA bypass, login export, cookie extraction, unattended scraping or actual ChatGPT GUI submission is included.

## Scope

The control plane is in `relay.py`; offline tests in `test_relay.py` and `test_relay_restart.py`. The latter uses a fresh Python process per CLI action and fictitious conversation URLs.

```powershell
cd C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012
python -B -m unittest -v test_relay test_relay_restart
```

**Expected baseline:** 11 original tests / 11 pass. Stage 2: 20 combined tests / 20 pass. Tests use isolated SQLite databases under the OS temp directory.

CLI usage: `python -B relay.py --help`. Root flag `--db` must precede subcommand.

- `prepare`: GPT queues one identity-bound request (project, chat URL, master epoch, turn, prompt SHA256).
- `claim`: Codex records activator identity and claim lease, before any browser I/O.
- `arm`: Codex records `MAY_HAVE_SUBMITTED` **before** any possible send. No automatic retry from this state.
- `observe`: append externally asserted evidence SHA and reference; remains `OBSERVED_UNVERIFIED`.
- `inspect` / `verify`: review state and hash-chain integrity.

## Acceptance levels

1. **PASS-OFFLINE:** deterministic, isolated transitions and hash-chain tests run successfully.
2. **NOT_RUN-PHYSICAL:** real Windows Chrome binding, trusted GPT response observation, two same-conversation turns, session resume, PID/module identity and 28/25 minute unattended operation.
3. **PRODUCTION: NOT_ACCEPTED.**

The `observe` command is intentionally NOT an attestation of real remote GPT output. Without an authenticated, separately validated host adapter it cannot upgrade to `GPT_ACCEPTED`. In particular it cannot automatically start a second round.

## Hard stop conditions

Wrong project/session/epoch/turn, stale/duplicate request, mismatched ChatGPT conversation, expired claim, uncertain submission, missing trusted observation, authentication problems, non-matching code/hash, untrusted host, or any CAPTCHA/login challenge. Stop and preserve evidence; never blindly click Send again.

## Local work record

2026-10-12 Stage 1: GPT-6 authored/patched isolated code; Codex ran fixed tests and recorded SHA256 (`11/11 PASS`). Stage 2: GPT-6 added master-epoch fencing, end-of-journal-state checks and cross-process tests. Codex ran `python -B -m unittest -v test_relay test_relay_restart`: `20/20 PASS`, exit `0`, 13.253s (unittest). No Chrome/GPT activation occurred. Stage 2 source hashes and durable receipt must be newly generated; Stage 1 hashes no longer describe the edited files. No production files changed.

Current isolated Windows paths:
- `C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012\relay.py`
- `C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012\test_relay.py`
- `C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012\ROLE_CONTRACT.md`

## Stage 2 physical binding blocker

Chrome Use CLI health and process existence are confirmed, but the current live ChatGPT conversation URL and driver-owned session are NOT bound to this experiment. A single real `chrome-use.exe session list` Codex delegation timed out; no trusted session result was returned. Do not guess, re-send or bind an arbitrary Chrome tab.

The physical browser activation adapter is absent. A real GPT auto-wake is **NOT_RUN**, regardless of the 20 passing offline tests. Before physical activation, independently verify a user-approved exact `https://chatgpt.com/c/...` URL, authenticated session, input idle state, and a trusted post-submit observation. Uncertainty blocks sending.
