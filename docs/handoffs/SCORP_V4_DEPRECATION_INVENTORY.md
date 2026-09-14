# SCORP V4 deprecation inventory

## Decision

All listed V3 and executor components are **soft-deprecated and retained**. No file,
state root, scheduled task, service, or browser registry was deleted or switched.
The fixed-string scan found live code, tests, installers, and documentation references
for every named component; deletion would break rollback or the current production
chain.

The V4 Chrome Use driver state is now the lifecycle authority for the candidate
browser seam. Legacy `sessions-v3.json` and `worker-conversation-pool-v3.json`
remain migration and rollback inputs; they are not dual-write authorities. V4
adds a `sessions` ledger to its own driver state, protects persistent
Master/Worker sessions, and retires only explicitly registered diagnostic
sessions. No cleanup command is allowed to use a global `session prune` or
`close --all` policy.

## Component classification

| Component | References | Current role | V4 treatment | Deletion gate |
|---|---:|---|---|---|
| `project_state_v3.py` | 6 | V3 project JSON authority | snapshot import only | cutover + archived rollback |
| `session_registry_v3.py` | 6 | actor/chat identity registry | migrate as immutable evidence | real-browser parity |
| `worker_conversation_pool_v3.py` | 8 | V3 Worker conversation leasing | replaced experimentally by leases/bindings | soak + cutover |
| `master_state_transition_v3.py` | 7 | V3 A transition rules | replaced experimentally by SQLite CAS | full migration proof |
| `durable_actor_transport_v3.py` | 9 | V3 browser delivery ledger | replaced experimentally by intents/outbox | real-browser + soak |
| `parallel_master_worker_relay_v3.py` | 9 | V3 scheduling/relay loop | replaced experimentally by two-slot scheduler | soak + cutover |
| `windows_mcp_actor_driver_v3.py` | 8 | current GUI transport | retained browser compatibility path | proven replacement |
| `executor-v4.ps1` | 3 | older executor | rollback compatibility | reference-free cutover |
| `executor-v4.1.ps1` | 12 | current executor path | not modified | separately authorized cutover |
| `runner-v4.ps1` | 4 | older runner | rollback compatibility | reference-free cutover |
| `runner-v4.1.ps1` | 10 | current runner path | not modified | separately authorized cutover |

## Protected runtime roots

| Root | References | Decision |
|---|---:|---|
| `C:\ScorpAgent\state-v3\active` | 15 | preserve; import only from a byte-hashed snapshot |
| `C:\ScorpAgent\state-v4` | 15 | preserve; no V4 transaction-core writes |
| `C:\ScorpAgent\gpt-native-v4` | 4 | preserve; no install or task-registration change |
| `C:\ScorpAgent\chatgpt-gui-bridge-runtime` | 7 | preserve; no registry or ledger mutation |

Counts are from a fixed-string scan of `scorp-agent` and `docs` in the candidate
worktree. References added by this inventory are included, so counts are evidence of
retention need rather than a claim of dead-code reachability.

## Anti-path-dependence check

Starting from zero, SQLite CAS, durable outbox, explicit leases, and deterministic
acceptance remain the preferred target over multiple mutable JSON authorities.
However, that architectural preference does not justify deleting the working V3
chain before real-browser parity, full candidate identity, migration evidence, the
requested short performance/recovery soak, rollback archival, and explicit cutover
approval exist. The short run is not evidence of 24-hour stability.
