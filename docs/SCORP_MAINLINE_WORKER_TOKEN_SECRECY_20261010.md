# P0 Master→Worker raw lease secrecy on main

Date 2026-10-10. This independent Draft is based directly on production `main` SHA `79610ae3f3864e133f750f0c9dfde40b17a91def`.

The local scheduler holds random bearer `lease_token` values, while the Master and direct Gateway previously accepted generated Worker prompts with no guard against accidentally embedding that token. A Worker browser message containing a raw token would disclose a local authorization credential to model/browser history and invalidate lease secrecy.

This narrow source gate rejects missing leases and rejects **any** Worker prompt containing the exact raw bearer token, before persistence or browser submit, at **both** `MasterAController._dispatch_claim` and `V4BridgeGateway.prepare_worker_intent`. No token is printed to logs. Two new isolated tests assert the refusal occurs without a browser-engine submit. Normal non-secret Worker prompts keep their existing behavior.

**Important boundary:** production `main` has a different `state_store.py` (1547 lines, no operator_generation/objective_generation) from isolated PR #26's 3211-line durable state machine. This patch only protects prompt secrecy. It does **not** import or claim generation-bound Worker LocalExecution authorization, full task-scoped human approval, genuine dual GPT session identities, or 24-hour host availability.

Actual Windows read-only host evidence: a LocalSystem Broker was Running Auto, but on-disk `broker_service.py` was unpinned to `main` and PR #29; old Master task properties could not be verified. Do not deploy, restart services, send old ambiguous prompts or modify R1.

No production writes or deployment performed. All tests are on ephemeral CI.
