param(
    [string]$ExecutorPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "executor-v4.1.ps1"),
    [string]$SchemaPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "executor-v4.schema.json")
)

$ErrorActionPreference = "Stop"

function Assert-True {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) { throw "V4_CRITICAL_REGRESSION_FAIL: $Name" }
    Write-Output "PASS $Name"
}

function Get-FunctionSlice {
    param([string]$Source, [string]$Name, [string]$NextName)
    $start = $Source.IndexOf("function $Name", [StringComparison]::Ordinal)
    if ($start -lt 0) { throw "function not found: $Name" }
    if ([string]::IsNullOrWhiteSpace($NextName)) { return $Source.Substring($start) }
    $finish = $Source.IndexOf("function $NextName", $start + 1, [StringComparison]::Ordinal)
    if ($finish -lt 0) { throw "next function not found: $NextName" }
    return $Source.Substring($start, $finish - $start)
}

if (-not (Test-Path -LiteralPath $ExecutorPath -PathType Leaf)) { throw "executor missing: $ExecutorPath" }
if (-not (Test-Path -LiteralPath $SchemaPath -PathType Leaf)) { throw "schema missing: $SchemaPath" }
$source = Get-Content -LiteralPath $ExecutorPath -Raw
$schemaText = Get-Content -LiteralPath $SchemaPath -Raw
$schema = $schemaText | ConvertFrom-Json

# A - local PREPARED state must exist before the remote RUNNING title mutation.
$prepare = Get-FunctionSlice $source "Prepare-QueuedIssue" "Reject-QueuedIssue"
$saveIndex = $prepare.IndexOf("Save-State `$state", [StringComparison]::Ordinal)
$guardedTitleIndex = $prepare.IndexOf("Set-IssueTitleFromExpectedVerified", [StringComparison]::Ordinal)
Assert-True ($saveIndex -ge 0 -and $guardedTitleIndex -ge 0 -and $saveIndex -lt $guardedTitleIndex) "A-state-before-remote-title"
Assert-True ($source.Contains('stage-ceq"RUNNING_TITLE_VERIFIED"'.Replace('\',''))) "A-recoverable-running-title-stage"

# B - recorded child identity must be bound to authoritative Win32_Process command line.
$child = Get-FunctionSlice $source "Test-ChildAlive" "Find-RunnerProcesses"
Assert-True ($child.Contains("Get-CimInstance Win32_Process")) "B-authoritative-cim-child-lookup"
Assert-True ($child.Contains("runner-v4.1.ps1") -and $child.Contains("gate_path") -and $child.Contains("envelope_path")) "B-runner-gate-envelope-identity"

# B2 - once a durable result exists, child teardown ambiguity must not override it.
$resultAware = Get-FunctionSlice $source "Test-MonitorChildAlive" "Find-RunnerProcesses"
$monitor = Get-FunctionSlice $source "Monitor-State" "Resume-State"
Assert-True ($resultAware.Contains("result_path") -and $resultAware.Contains("Test-Path") -and $resultAware.Contains("Test-ChildAlive") -and $resultAware.Contains("catch")) "B2-durable-result-wins-child-exit-race"
Assert-True ($monitor.Contains("Test-MonitorChildAlive") -and -not $monitor.Contains("while(Test-ChildAlive `$State)")) "B2-monitor-uses-result-aware-liveness"

# B3 - an unreadable/malformed durable result must fail closed instead of looping forever in RUNNING.
$finalize = Get-FunctionSlice $source "Finalize-State" "Block-State"
Assert-True ($finalize.Contains("ConvertFrom-Json") -and $finalize.Contains("AMBIGUOUS_LOCAL: invalid durable result JSON")) "B3-invalid-result-json-fails-closed"
Assert-True ($finalize.Contains("try") -and $finalize.Contains("catch")) "B3-finalize-result-parse-guarded"

# B4 - runner emits UTF-8 no-BOM result JSON; WinPS5.1 consumer must decode it explicitly.
$utf8ResultPattern = 'Get-Content\s+-LiteralPath\s+\(\[string\]\$State\.result_path\)\s+-Raw\s+-Encoding\s+UTF8\s*\|\s*ConvertFrom-Json'
Assert-True ([regex]::IsMatch($finalize,$utf8ResultPattern,[Text.RegularExpressions.RegexOptions]::IgnoreCase)) "B4-result-consumer-explicit-utf8"
$utf8Fixture = Join-Path $env:TEMP ("scorp-v4-utf8-"+[guid]::NewGuid().ToString("N")+".json")
try {
    $nonAscii = [string][char]0x627E
    $fixtureJson = '{"message":"' + $nonAscii + '"}'
    [IO.File]::WriteAllText($utf8Fixture,$fixtureJson,(New-Object Text.UTF8Encoding($false)))
    $utf8Parsed = Get-Content -LiteralPath $utf8Fixture -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True ([string]$utf8Parsed.message -ceq $nonAscii) "B4-utf8-nobom-fixture-roundtrip"
} finally { Remove-Item -LiteralPath $utf8Fixture -Force -ErrorAction SilentlyContinue }

# B5 - direct-control shells may omit COMPUTERNAME; production mutex identity must still bind the machine.
$mutexName = Get-FunctionSlice $source "Get-DefaultMutexName" "Enter-Mutex"
Assert-True ($mutexName.Contains("COMPUTERNAME") -and $mutexName.Contains("[Environment]::MachineName") -and $mutexName.Contains("IsNullOrWhiteSpace")) "B5-mutex-machine-name-fallback"

# C - discovery authority failure must fail closed, never become an empty match set.
$find = Get-FunctionSlice $source "Find-RunnerProcesses" "Ensure-ClaimExactlyOnce"
Assert-True ($find.Contains("AMBIGUOUS_LOCAL:") -and $find.Contains("throw")) "C-discovery-failure-fails-closed"

# D - result identity is task + issue + action + claim, locally and remotely.
$parse = Get-FunctionSlice $source "Parse-ResultIdentity" "Get-MatchingClaims"
$match = Get-FunctionSlice $source "Get-MatchingResults" "Set-IssueTitleVerified"
Assert-True ($parse.Contains("TaskId") -and $parse.Contains("task")) "D-result-parser-binds-task"
Assert-True ($match.Contains("TaskId") -and $match.Contains("TaskId-ceq")) "D-remote-result-match-binds-task"
Assert-True ($finalize.Contains("result.task_id") -and $finalize.Contains("State.task_id")) "D-local-result-finalize-binds-task"

# K — privileged execution must be an explicit, narrow broker capability rather than implicit elevation.
$actionKinds = @($schema.properties.action_kind.enum | ForEach-Object { [string]$_ })
Assert-True ($actionKinds -ccontains "privileged_broker") "K-schema-explicit-privileged-broker-kind"
$validate = Get-FunctionSlice $source "Validate-Envelope" "Copy-EnvelopeForExecution"
Assert-True ($validate.Contains('"privileged_broker"')) "K-executor-explicit-privileged-broker-kind"
Assert-True ($validate.Contains('action_kind-ceq"privileged_broker"') -and $validate.Contains('safety_class-cne"approved_admin"')) "K-privileged-broker-requires-approved-admin"
Assert-True ($validate.Contains('payload.PSObject.Properties') -and $validate.Contains('"operation"') -and $validate.Contains('"params"')) "K-privileged-broker-payload-is-operation-params-only"
foreach ($operation in @("identity.get","service.get","service.restart","task.get","task.run","file.write","registry.set")) {
    Assert-True ($validate.Contains('"'+$operation+'"')) ("K-privileged-operation-allowlist-"+$operation)
}
Assert-True (-not $validate.Contains('"process.run"')) "K-no-privileged-process-run"
Assert-True (-not $validate.Contains('payload.script')) "K-no-privileged-shell-payload"
Assert-True (-not $validate.Contains('payload.executable')) "K-no-privileged-executable-payload"

# M — only privileged_broker may reconcile an ambiguous dead runner, and relaunch is bounded to one.
$reconcile = Get-FunctionSlice $source "Ensure-BrokerReconciliationChild" "Ensure-ClaimExactlyOnce"
$resume = Get-FunctionSlice $source "Resume-State" "Prepare-QueuedIssue"
$ensure = Get-FunctionSlice $source "Ensure-ChildStarted" "New-ResultReport"
$prepare = Get-FunctionSlice $source "Prepare-QueuedIssue" "Reject-QueuedIssue"
Assert-True ($reconcile.Contains('action_kind-cne"privileged_broker"')) "M-reconcile-broker-only"
Assert-True ($reconcile.Contains('broker_reconcile_attempts') -and $reconcile.Contains('-ge1')) "M-reconcile-bounded-once"
Assert-True ($reconcile.Contains('Find-RunnerProcesses') -and $reconcile.Contains('Launch-GatedChild')) "M-reconcile-authoritative-discovery-before-launch"
$attemptIndex = $reconcile.IndexOf('broker_reconcile_attempts=[int]$State.broker_reconcile_attempts+1',[StringComparison]::Ordinal)
$saveReconcileIndex = if($attemptIndex-ge0){$reconcile.IndexOf('Save-State $State',$attemptIndex,[StringComparison]::Ordinal)}else{-1}
$launchIndex = $reconcile.IndexOf('Launch-GatedChild $State',[StringComparison]::Ordinal)
Assert-True ($attemptIndex -ge 0 -and $saveReconcileIndex -gt $attemptIndex -and $launchIndex -gt $saveReconcileIndex) "M-reconcile-attempt-durable-before-launch"
Assert-True ($resume.Contains('action_kind-ceq"privileged_broker"') -and $resume.Contains('Ensure-BrokerReconciliationChild $State') -and $resume.Contains('Monitor-State $State')) "M-resume-broker-reconciliation-route"
Assert-True ($monitor.Contains('action_kind-ceq"privileged_broker"') -and $monitor.Contains('Ensure-BrokerReconciliationChild $State')) "M-monitor-broker-reconciliation-route"
Assert-True ($resume.Contains('throw "AMBIGUOUS_LOCAL: recorded child is gone, gate exists, and no durable result exists"')) "M-ordinary-resume-remains-fail-closed"
Assert-True ($ensure.Contains('start gate exists but no live child/result')) "M-ordinary-ensure-remains-fail-closed"
Assert-True ($prepare.Contains('broker_reconcile_attempts=0')) "M-reconcile-attempt-state-initialized"

# N - rejection persistence must be namespaced by repository as well as issue number.
$rejectionPath = Get-FunctionSlice $source "Get-RejectionLedgerPath" "Reject-QueuedIssue"
$reject = Get-FunctionSlice $source "Reject-QueuedIssue" "Assert-Test"
Assert-True ($rejectionPath.Contains('$Repo') -and $rejectionPath.Contains('IssueNumber')) "N-rejection-ledger-binds-repo-and-issue"
Assert-True ($rejectionPath.Contains('Get-StringSha256')) "N-rejection-ledger-repo-key-is-deterministic"
Assert-True ($reject.Contains('Get-RejectionLedgerPath') -and -not $reject.Contains('("issue-{0}.json"-f$n)')) "N-reject-uses-repo-scoped-ledger-path"

Assert-True (-not ($source -match '(?i)codex\s+exec|codex\.exe')) "no-codex-invocation"
Write-Output "V4_CRITICAL_HARDENING_PASS"