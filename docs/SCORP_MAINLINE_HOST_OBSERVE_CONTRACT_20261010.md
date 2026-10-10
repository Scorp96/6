# Reusable P0 read-only Windows authority report (no production writes)

Date: 2026-10-10. **Isolated Draft on real main**. This is an optional operator-directed host observation script, not a scheduler/installer, executor, recovery engine or privileged control entrypoint.

`scorp-agent/verify-host-p0-readonly.ps1` reads only:
- metadata for **one named** SCORP Broker service and scheduled task;
- current caller's effective administrator token status, without printing identity;
- SHA-256 of exactly `broker_service.py`, `broker_core.py`, `broker_ops.py`, `broker_client.py` at the designated Broker source directory;
- ACL metadata for the known Broker HMAC key, **never key contents** (broad-principal possibility, not effective-access claim).

It requires four separately reviewed expected SHA-256 values and a full Git source commit identifier to label disk hashes as matching. Missing pins are `UNPINNED`; false matches cannot authorize release. It produces one redacted compact JSON record only: no usernames, SIDs, paths, PIDs, commands, keys, browser content, cookies, .codex or chats. No service/task/file/process mutations or network activity are part of the script.

**Critical:** Even if all four disk hashes match a reviewed Git commit, this **does NOT verify the currently running LocalSystem Python process has imported those files**. There is deliberately no attempt to inject code into that process, examine memory, read arguments/environments, restart it, send a test mutation, or bypass access-denied Windows scheduled task metadata. The report always sets `running_broker_loaded_python_modules_attested=false` and `production_cutover_authorized=false`.

Windows CI supplies disposable fake Broker source files under RUNNER_TEMP. It tests that 4/4 known hashes are marked MATCH without ever authorizing release, while omitted pins produce UNPINNED/BLOCKED, and checks the source AST has no known file/process/service mutation commands. Only ephemeral CI fixtures are written; the audit script itself is read-only.

For the user's actual Windows host, an appropriately authorized operator must separately review running-service module provenance and Task Scheduler ACLs and present rollback/maintenance approval before any R1 change. The previously observed real Broker is Running as LocalSystem/Auto with three source hashes differing from candidate and no verified runtime module identity. No real second authenticated GPT Worker session and no 24h soak have been established.
