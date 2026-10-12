# GPT-6 executor / Codex activator-recorder (experimental)

**Status:** offline transaction core, NOT production, NOT a real browser GPT wake-up.

## Responsibility boundary

- GPT-6 does research, tool execution, architecture, source edits, decisions, acceptance.
- Codex only activates bound GPT requests through a future separately reviewed browser/host adapter, executes fixed verification commands as an operator, and records immutable evidence. It must not become a substitute reasoning master.
- SQLite owns request identity, activation lease, submit ambiguity and append-only hash-chained receipt records.
- Neither Codex Goals nor Codex App Server can automatically create a turn in the same ChatGPT web conversation by themselves.
- Browser activation remains disabled. No CAPTCHA bypass, login export, cookie extraction, unattended scraping or actual ChatGPT GUI submission is included.

## Scope

The control plane is in `relay.py` and offline unittest cases in `test_relay.py`.

```powershell
cd C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012
python -B -m unittest -v test_relay
```

**Expected:** 11 tests / 11 pass. Tests use isolated SQLite databases under the OS temp directory.

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

2026-10-12: GPT-6 authored and patched isolated code. Codex was constrained to run the requested local unittest and return hash/exit evidence. Last completed fixed run: `11/11 PASS`, exit `0`. No production files changed.

Current isolated Windows paths:
- `C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012\relay.py`
- `C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012\test_relay.py`
- `C:\Users\scorp\Documents\Codex\isolated-tests\gpt6-codex-activator-20261012\ROLE_CONTRACT.md`
