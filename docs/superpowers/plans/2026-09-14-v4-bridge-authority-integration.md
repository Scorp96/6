# SCORP V4 bridge authority integration

## Goal

Connect the existing ChatGPT GUI bridge to the V4 SQLite transaction core without
making the legacy JSON relay the authority or changing the production scheduled
task. The candidate must expose one opt-in runtime entry point, keep the GitHub
queue explicit, surface daemon failures, and preserve browser ambiguity gates.

## Scope

1. Add a single queue configuration with `Scorp96/666` as the candidate default;
   reject the historical control-plane repository unless an explicit read-only
   migration mode is selected.
2. Harden the candidate bridge's file writes and GitHub comment reads, and make
   daemon errors observable through a health callback and bounded failure stop.
3. Add a V4 gateway that creates/loads a root contract, schedules a task graph,
   and routes browser submissions through `StateStore` and `BrowserAdapter`.
4. Add fake-engine tests and a short simulated handoff/recovery check. Do not
   invoke a real browser, GitHub mutation, or production scheduler.

## Verification

- New tests are written first and must fail before implementation.
- Run the new bridge tests and all existing V4 tests with `unittest`.
- Run bridge regression tests and `compileall`.
- Report production switch, real browser, authentication, and long-soak checks
  as unverified unless separately executed.
