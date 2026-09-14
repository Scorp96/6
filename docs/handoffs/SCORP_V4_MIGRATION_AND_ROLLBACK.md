# SCORP V4 migration and rollback boundary

## Authority model

V4 has one mutable authority: its SQLite database. Legacy JSON is accepted only as
an immutable `IMPORTED_SNAPSHOT`; V4 JSON output is labelled
`READ_ONLY_EXPORT`. Neither form may be written back into the V3 runtime or used as
a second live authority.

An import records the exact source path, byte length, modification timestamp,
SHA-256, detected schema name, status, and conflicts in one SQLite transaction.
The importer never edits the source file and never invents V4 transitions, events,
task results, evidence, or state-version history. A missing root contract,
acceptance contract, project identity, malformed JSON, or conflict with an existing
V4 contract produces `BLOCKED` and preserves the conflict.

## Legacy rollback material

The following V3/runtime stores and components remain soft-deprecated rollback
material. They are not deleted or modified by the experimental V4 installation:

- `C:\ScorpAgent\state-v3\active` project-state JSON and journals
- `C:\ScorpAgent\state-v4` executor request/result state
- `C:\ScorpAgent\gpt-native-v4` executor runtime and manifests
- `C:\ScorpAgent\chatgpt-gui-bridge-runtime` bridge state, including conversation registries
- `project_state_v3.py`
- `session_registry_v3.py`
- `worker_conversation_pool_v3.py`
- `master_state_transition_v3.py`
- `durable_actor_transport_v3.py`
- `parallel_master_worker_relay_v3.py`
- `windows_mcp_actor_driver_v3.py`
- `executor-v4.ps1`, `executor-v4.1.ps1`, `runner-v4.ps1`, and `runner-v4.1.ps1`

## Migration sequence

1. Stop before import if the root or acceptance contract cannot be recovered and
   independently identified.
2. Hash and import each named JSON source without changing it.
3. Resolve every recorded conflict explicitly; never overwrite the current V4
   contract or synthesize missing history.
4. Run AC01 through AC11 against one candidate commit and one artifact manifest in
   the isolated lab root.
5. Run the real short AC12 performance/recovery soak and preserve its duration,
   throughput, recovery, duplicate, error, and environment measurements.
6. Only a separately authorized production cutover may designate the V4 database as
   production authority.

## Rollback

Before production cutover, rollback means stop the experimental V4 lab process and
continue using the untouched existing production runtime. No reverse conversion is
needed because V4 has not written into it. After an authorized cutover, rollback
requires the archived protected-path manifests, original snapshot hashes, a named
recovery point, and a verified operator procedure; that future operation is outside
this experimental implementation.

Legacy deletion is forbidden until reference scanning, migration tests, full
regression, archived rollback evidence, and explicit production-cutover approval all
exist. Short-soak success does not prove long-duration stability and does not by
itself authorize deletion or cutover.
