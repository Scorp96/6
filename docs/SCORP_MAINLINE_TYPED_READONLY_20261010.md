# Mainline P0: typed read-only executor envelopes and side-effect denial

Date: 2026-10-10. **Draft only, derived from production `main@79610ae3f3864e133f750f0c9dfde40b17a91def`.**

Original main `executor-v4.1.ps1` and `runner-v4.1.ps1` accepted generic PowerShell/process/git actions. A `read_only` precondition on those actions does **not** itself enforce an OS-level read-only sandbox: those executables can write, stop services, or mutate the filesystem.

This isolated, main-derived source transplant ports the previously reviewed P0 containment:
- Reject generic command types `powershell`, `process` and `git` when an advertised read-only/no-production-writes constraint is present. Do not treat flags or a request text as a sandbox.
- Reject write operations including file replacement and privileged Broker mutation when declared read-only.
- Introduce `diagnostic_readonly` with only `chrome_resource_summary`, `broker_service_status`, `executor_task_status`. It reports bounded status without creating unrestricted command execution authority.
- Check constraints at **both** executor envelope acceptance and runner dispatch; future relay implementations must not accidentally bypass the outer check.
- The Windows PowerShell 5.1 fixture actually invokes the runner with carefully sandboxed, denied operations and proves no file marker is created; it also tests unchanged simple `file_read` and `health` behavior.

This does **not** give OS-enforced isolation to arbitrary scripts: generic command actions remain powerful when explicitly permitted through an independently approved write pathway. Existing running Windows R1 and the LocalSystem Broker **must not** be replaced merely because this offline test passes.

Live-host evidence from Codex Bridge: the LocalSystem Broker remains Running Auto, installed Broker code is not proven to match the reviewed source, the Scheduled Task's effective authority is unknown, and two authenticated browser Workers plus 24-hour host soak remain unverified. The main Worker generation schema is not present.

**Status:** P0 source-only negative-test candidate; no merge, no production deployment, no browser send or privilege elevation.
