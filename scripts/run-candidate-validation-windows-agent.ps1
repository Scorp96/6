param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[a-f0-9]{40}$')]
    [string]$ExpectedCommit,
    [string]$RepoRoot = 'C:\ScorpAgent\experiments\r2-host-terminal-security-20261009',
    [string]$Python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'

# Safe replacement for Windows PowerShell 5.1 native-command stderr handling.
# Never runs a browser, sends prompts, updates production SQLite or scheduled tasks.
$experiments = [IO.Path]::GetFullPath('C:\ScorpAgent\experiments')
$repo = [IO.Path]::GetFullPath($RepoRoot)
if (-not $repo.StartsWith($experiments + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'NON_ISOLATED_WORKSPACE_REFUSED'
}
$relative = $repo.Substring($experiments.Length + 1)
if ($relative.Contains('\') -or -not $relative.StartsWith('r2-', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'ISOLATED_WORKSPACE_DEPTH_INVALID'
}
if ($relative -eq 'r2-gpt-session-audit-20261009') {
    throw 'ACTIVE_OBSERVER_CHECKOUT_PROTECTED'
}
if (-not (Test-Path -LiteralPath $repo -PathType Container)) { throw 'ISOLATED_REPO_MISSING' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'ISOLATED_PYTHON_MISSING' }
$head = (& git -C $repo rev-parse HEAD 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $head -cne $ExpectedCommit) { throw 'ISOLATED_SHA_MISMATCH' }
$dirty = (& git -C $repo status --porcelain 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'ISOLATED_WORKTREE_DIRTY' }

$source = @'
import compileall
import io
import json
import os
import pathlib
import sys
import time
import unittest

repo = pathlib.Path(os.environ["SCORP_ISOLATED_REPO_ROOT"])
agent = repo / "scorp-agent"
sys.path.insert(0, str(agent))
results = []
start = time.monotonic()

def run(group, workdir, patterns):
    before = os.getcwd()
    try:
        os.chdir(str(workdir))
        sys.path.insert(0, str(workdir))
        for pattern in patterns:
            tests = unittest.defaultTestLoader.discover(
                start_dir=str(workdir / "tests"),
                pattern=pattern,
                top_level_dir=str(workdir / "tests"),
            )
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(tests)
            results.append({
                "group": group, "pattern": pattern, "tests": result.testsRun,
                "errors": len(result.errors), "failures": len(result.failures),
                "skipped": len(result.skipped), "pass": result.wasSuccessful(),
            })
    finally:
        os.chdir(before)

run("v4", agent / "master_a_dynamic_v4", ["test_*.py"])
run("bridge", agent / "chatgpt-gui-bridge", ["test_*.py"])
run("broker", agent / "privileged-broker", [
    "test_broker_core.py", "test_broker_client.py",
    "test_broker_ops.py", "test_install_contract.py",
])
compiled = all(compileall.compile_dir(str(path), quiet=1) for path in (
    agent / "master_a_dynamic_v4",
    agent / "chatgpt-gui-bridge",
    agent / "privileged-broker",
))
total = sum(row["tests"] for row in results)
group_counts = {
    group: sum(row["tests"] for row in results if row["group"] == group)
    for group in ("v4", "bridge", "broker")
}
# Suite floors for this isolated candidate; changes require fresh full CI.
# Fail if an entire module/test suite silently vanishes. Future deliberate
# test removals require an explicit baseline change and new acceptance.
minimums = {"v4": 584, "bridge": 727, "broker": 29}
passed = (
    compiled and total >= 1340
    and all(group_counts[name] >= count for name, count in minimums.items())
    and all(row["pass"] and not row["skipped"] for row in results)
)
record = {
    "protocol": "scorp.win-agent-validation/1",
    "sha": os.environ["SCORP_EXPECTED_SHA"],
    "groups": results,
    "test_count": total,
    "test_group_counts": group_counts,
    "minimum_group_counts": minimums,
    "compile_pass": compiled,
    "pass": passed,
    "seconds": round(time.monotonic() - start, 1),
    "browser_send": "NOT_ATTEMPTED",
    "production_writes": "NONE",
}
print(json.dumps(record, sort_keys=True, separators=(",", ":")))
sys.exit(0 if passed else 5)
'@

$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = $Python
$info.Arguments = '-B -c "import sys; exec(sys.stdin.read())"'
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$info.EnvironmentVariables['PYTHONPATH'] = (Join-Path $repo 'scorp-agent')
$info.EnvironmentVariables['SCORP_ISOLATED_REPO_ROOT'] = $repo
$info.EnvironmentVariables['SCORP_EXPECTED_SHA'] = $ExpectedCommit

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $info
if (-not $proc.Start()) { throw 'ISOLATED_PYTHON_PROCESS_START_FAILED' }
$proc.StandardInput.Write($source)
$proc.StandardInput.Close()
$stdoutTask = $proc.StandardOutput.ReadToEndAsync()
$stderrTask = $proc.StandardError.ReadToEndAsync()
if (-not $proc.WaitForExit(220000)) {
    $proc.Kill()
    throw 'ISOLATED_VALIDATION_TIMEOUT'
}
$stdout = $stdoutTask.Result.Trim()
$stderr = $stderrTask.Result
$finalLine = @($stdout -split '\r?\n' | Where-Object {
    $_ -match '"protocol":"scorp.win-agent-validation/1"'
}) | Select-Object -Last 1
$valid = $false
$report = $null
try {
    $report = $finalLine.Trim() | ConvertFrom-Json -ErrorAction Stop
    $valid = ($report.sha -ceq $ExpectedCommit -and $report.browser_send -eq 'NOT_ATTEMPTED')
}
catch { $valid = $false }
$out = @{
    protocol = 'scorp.win-agent-validation-wrapper/1'
    sha = $head
    test_report = $report
    report_valid = $valid
    stderr_present = [bool]$stderr
    exit_code = $proc.ExitCode
    browser_send = 'NOT_ATTEMPTED'
    production_writes = 'NONE'
}
$out | ConvertTo-Json -Compress -Depth 8
if (-not $valid -or -not $report.pass -or $proc.ExitCode -ne 0) {
    throw 'ISOLATED_VALIDATION_FAILED'
}
