# P0: Read-only claims must not be interpreted as enforcement

As of 2026-10-10; base commit `988c0e8466d85f42eb33822eee84a1d4f3f055ac`. Isolated proposal, not a Windows production update.

## Defect

In task issue #2511 on `Scorp96/scorp-control-plane`, the envelope for an unrestricted `powershell` script advertised `expected_preconditions: {read_only:true,no_production_writes:true}`. The V4 parent validates action/safety class and passes the envelope to the runner, but previously never interpreted those safety claims. A policy in JSON is not an OS sandbox. No malicious behavior is alleged for task #2511; the example demonstrates the missing enforcement boundary.

## Containment

Both parent admission and child runner now reject un-enforceable safety promises for unrestricted `powershell`, `process` and `git` actions. Claims validated: `read_only`, `no_production_writes`, `no_process_kill`, `no_browser_submit`, `no_browser_navigation`, `no_chrome_use_commands`. Non-boolean values are rejected. Mutating built-in file operations and mutating Broker actions contradicting read-only/no-writes are rejected. Safe built-in `file_read` and `health` remain possible.

The parent rejects before action reservation. The child independently rejects before its start gate, returning `PRECONDITION_FAILED` and exit code 3. The direct Runner isolation tests verify no canary side effect and preserved read-only actions.

## Warnings and blockers

- All previous arbitrary PowerShell diagnostic tasks which asserted `read_only:true` will be BLOCKED. This is an intentional, incompatible safety change: do not deploy until an equivalent safe deterministic diagnostic module is available.
- This does not make unrestricted scripts safe when no preconditions are asserted; they remain a P0 permission/separation problem.
- Unknown additional safety assertions are not a guarantee; this patch enforces only the enumerated claims. External approval is still needed for other high-risk operations.
- Generic runner user token and actual production ACLs are unverified. CI is offline and cannot prove real machine constraints.
- No production tasks, process kills, browser sessions, or Windows services were touched.

## Release gate

Pass Windows PowerShell 5.1 parse and negative behavior tests, then the complete existing 1605+ source/Windows candidate suite. Review current queue for claimed-read-only shell tasks; do not replay blocked/ambiguous tasks. Replace them with bounded deterministic typed read-only collectors run without administrator privileges. Revisit the literal USER_APPROVED_FULL_CONTROL token and independent human authority before allowing generic privileged execution.
