# SCORP P0 Lean Integrated Review — NOT a Production Release

As of 2026-10-10. This new isolated branch is compared against the original PR #12 P0 containment baseline. It groups the verified safety work from PRs #13–#23 into one human-review diff and retains **one** comprehensive offline Windows CI workflow on this branch. The ten superseded phase-specific workflow YAML files are deleted from this *new isolated branch only*; original Draft PR histories and their green CI evidence remain intact.

## Required source review

1. `scorp-agent/master_a_dynamic_v4/master_controller.py`, `state_store.py`, `session_admission.py`, and `chatgpt-gui-bridge/v4_bridge_gateway.py`: Worker claim lease, original permission generations, browser prompt secrecy, independent physical session boundary
2. `scorp-agent/executor-v4.1.ps1`, `runner-v4.1.ps1`, `executor-v4.schema.json`: read-only operations, no generic shell side effects
3. `scorp-agent/bootstrap-v4.ps1`, `install.ps1`: fail before first write for unsafe scheduled-task principal and no replacement of original R1; fresh Limited task remains Disabled until human approval
4. `scorp-agent/privileged-broker/broker_service.py`, `install-broker.ps1`: only three new read-only operations, all others denied before backend validation; no default install, no existing service replacement, four SHA-256 source pins and rollback ownership fence
5. `scripts/scorp_p0_release_verify.py`, `scripts/scorp_p0_release_pin_plan.py`: original 7-file manifest remains BLOCKED; immutable exact-commit dual hashes can be inspected without updating any production file

## Blocking evidence

- 4/7 legacy release Git blob hashes stale; 7/7 SHA-256 values unpinned. Review-only GitHub artifact is not a deployment certificate
- Windows #2511 last actual local result: `original_master_ambiguous=1`, `v4_browser_binding_count=0`, `worker2_real_session_host_attested=false`, `model_calls=0`
- No independent task-scoped human authorization secret to enable SYSTEM Broker mutations; shared executor HMAC is not independent human approval
- No installed Windows effective-token / ACL attestations on live host, no proven reversible R1-to-R2 migration
- No two independently authenticated GPT worker sessions, no trusted TURN_FINAL_CONFIRMED issuer, and no real 24-hour duration soak

The project is **not ready for cutover**, even if the single Windows workflow succeeds. No production merge/release/installer invocation/Chrome send is authorized by this review branch.
