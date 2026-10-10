# Mainline P0 Bootstrap: verify **effective** Windows token before first write

Date 2026-10-10. Source-only, production main parent `79610ae3f3864e133f750f0c9dfde40b17a91def`.

A Scheduled Task configured as `-RunLevel Limited` **does not prove** a manually invoked Bootstrap process is itself unelevated. The already-reviewed P0 Bootstrap checked task principal, built-in SYSTEM/Admin SID and UAC, but not the effective high-integrity Administrator membership of a non-built-in elevated account.

This isolated source fix additionally checks `WindowsPrincipal.IsInRole(WindowsBuiltInRole.Administrator)` on the **current process token**, after UAC/task checks but before `preflightPassed=true` and first staged file write. A high-privilege process aborts with `P0_UNSAFE_EFFECTIVE_TOKEN` rather than trusting the Task Scheduler metadata.

Windows CI parses the PowerShell 5.1 script and verifies source ordering, the original Limited bootstrap checks and complete R2/GUI suites. This is not a live proof of an administrator process failure on the user's PC; a controlled elevated and unelevated Windows fixture canary is still needed before deployment.

The active Windows task cannot presently be attested via read-only Codex Bridge metadata. No task elevation, ACL change, Broker service restart, R1 migration or browser send is permitted by this Draft.
