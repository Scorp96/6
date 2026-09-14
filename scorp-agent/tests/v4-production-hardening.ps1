param(
    [string]$ExecutorPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "executor-v4.1.ps1"),
    [string]$RunnerPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "runner-v4.1.ps1"),
    [string]$BootstrapPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "bootstrap-v4.ps1")
)

$ErrorActionPreference = "Stop"
function Assert-True { param([bool]$Condition,[string]$Name); if(-not $Condition){throw "V4_PRODUCTION_REGRESSION_FAIL: $Name"}; Write-Output "PASS $Name" }
function Get-Slice {
    param([string]$Source,[string]$Start,[string]$Finish)
    $i=$Source.IndexOf("function $Start",[StringComparison]::Ordinal); if($i-lt0){throw "function missing: $Start"}
    $j=$Source.IndexOf("function $Finish",$i+1,[StringComparison]::Ordinal); if($j-lt0){throw "next function missing: $Finish"}
    return $Source.Substring($i,$j-$i)
}

$executor=Get-Content -LiteralPath $ExecutorPath -Raw
$runner=Get-Content -LiteralPath $RunnerPath -Raw
$bootstrap=Get-Content -LiteralPath $BootstrapPath -Raw
# O 闁?executor GitHub transport must preserve JSON bytes under Windows PowerShell 5.1.
$executorGh=Get-Slice $executor "Invoke-Gh" "Convert-JsonToFlatArray"
Assert-True ($executor.Contains("function ConvertTo-NativeArgument") -and $executorGh.Contains("ConvertTo-NativeArgument")) "O-executor-native-arguments-quoted"
Assert-True ($executorGh.Contains("System.Diagnostics.ProcessStartInfo") -and $executorGh.Contains("RedirectStandardOutput") -and $executorGh.Contains("RedirectStandardError") -and -not $executorGh.Contains('& gh')) "O-executor-gh-streams-isolated"
Assert-True ($executorGh.Contains("StandardOutputEncoding") -and $executorGh.Contains("StandardErrorEncoding") -and $executorGh.Contains("UTF8Encoding")) "O-executor-gh-explicit-utf8"

# E — block recovery stays durable until cloud terminal evidence is verified.
$blockIncident=Get-Slice $executor "Ensure-BlockedIncidentExactlyOnce" "Finalize-State"
$block=Get-Slice $executor "Block-State" "Monitor-State"
Assert-True ($block.Contains("Ensure-BlockedIncidentExactlyOnce") -and $blockIncident.Contains("SCORP_EXEC_BLOCKED") -and $blockIncident.Contains("block_phase") -and $blockIncident.Contains("Get-MatchingBlockComments")) "E-structured-block-incident"
Assert-True ($block.Contains("Save-State `$State") -and $block.IndexOf("Save-State `$State",[StringComparison]::Ordinal)-lt$block.IndexOf("Clear-State",[StringComparison]::Ordinal)) "E-active-state-preserved-until-terminal"
Assert-True ($block.Contains("Close-IssueVerified")) "E-blocked-issue-closed-after-verification"

# F — rejected queued issues have a durable replay-suppression ledger.
$reject=Get-Slice $executor "Reject-QueuedIssue" "Assert-Test"
Assert-True ($executor.Contains("rejection-ledger") -and $reject.Contains("rejection_phase")) "F-rejection-ledger"
Assert-True ($reject.Contains("terminal_verified_at=`$null") -and $reject.Contains("SCORP_EXEC_REJECTED") -and $reject.Contains("Get-AuthoritativeComments")) "F-rejection-comment-deduplicated"
Assert-True ($reject.Contains("Close-IssueVerified")) "F-rejected-issue-closed-after-verification"
Assert-True ($executor.Contains('if(Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf){throw}') -and $executor.Contains('Reject-QueuedIssue $issue')) "F-active-state-bypasses-rejection"

# G — idempotency is global to task_id + action_id and binds the exact remote envelope.
Assert-True ($executor.Contains("action-ledger") -and $executor.Contains("Reserve-ActionIdentity")) "G-global-action-ledger"
Assert-True ($executor.Contains("Finalize-ActionLedger")) "G-terminal-ledger-update"
$reserve=Get-Slice $executor "Reserve-ActionIdentity" "Finalize-ActionLedger"
Assert-True ($reserve.Contains("EnvelopeSha256") -and $reserve.Contains("envelope_sha256") -and $reserve.Contains("GLOBAL_IDEMPOTENCY")) "G-envelope-hash-bound-ledger"
Assert-True ($executor.Contains("Get-StringSha256") -and $executor.Contains("envelope_sha256")) "G-envelope-hash-persisted"

# H — bounded file reads/native output and exact durable start-gate semantics.
$fileRead=Get-Slice $runner "Read-FileBounded" "Write-FileAtomic"
$native=Get-Slice $runner "Invoke-Native" "Wait-StartGate"
$gate=Get-Slice $runner "Wait-StartGate" "Add-NativeEvidence"
$tail=Get-Slice $runner "Get-BoundedTailText" "Assert-ApprovedPath"
Assert-True (-not $fileRead.Contains("ReadAllBytes") -and $fileRead.Contains("FileStream")) "H-bounded-file-read"
Assert-True (-not $native.Contains('$output = &') -and $native.Contains("stdout_log_path") -and $native.Contains("stderr_log_path")) "H-streamed-native-output"
Assert-True ($tail.Contains('TrimStart([char]0xFEFF)') -and $tail.Contains('$start-eq0')) "H-native-tail-strips-leading-bom"
Assert-True ($gate.Contains('Trim()') -and $gate.Contains('-ceq"GO"')) "H-exact-go-start-gate"
Assert-True ($runner.Contains("runner_pid") -and $runner.Contains("runner_started_at")) "H-runner-identity-evidence"


# H2 — native programs may write diagnostic stderr and still exit 0; WinPS5.1 must not turn that into runner failure.
$nativeFixture=Join-Path $env:TEMP ("scorp-v4-native-stderr-"+[guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $nativeFixture|Out-Null
try {
    $emit=Join-Path $nativeFixture 'emit-stderr.ps1'; $envPath=Join-Path $nativeFixture 'envelope.json'; $resultPath=Join-Path $nativeFixture 'result.json'; $logPath=Join-Path $nativeFixture 'runner.log'
    [IO.File]::WriteAllText($emit,"[Console]::Out.WriteLine('SCORP_NATIVE_STDOUT_OK'); [Console]::Error.WriteLine('SCORP_NATIVE_STDERR_OK'); exit 0",(New-Object Text.UTF8Encoding($false)))
    $ps="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $envObj=[ordered]@{protocol_version='scorp.exec/v4';task_id='test/native-stderr';issue_number=1;claim_token='claim-native-stderr';action_id='native-stderr-exit0';action_kind='process';timeout_seconds=30;safety_class='standard';payload=[ordered]@{executable=$ps;argv=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$emit)}}
    [IO.File]::WriteAllText($envPath,($envObj|ConvertTo-Json -Depth 12),(New-Object Text.UTF8Encoding($false)))
    & $ps -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $RunnerPath -EnvelopePath $envPath -ResultPath $resultPath -LogPath $logPath
    $runnerCode=$LASTEXITCODE; $nativeResult=Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8|ConvertFrom-Json
    Assert-True ($runnerCode-eq0 -and [string]$nativeResult.status-ceq'SUCCEEDED' -and [int]$nativeResult.exit_code-eq0) "H2-native-stderr-exit0-succeeds"
    Assert-True ([string]$nativeResult.evidence.stderr_tail -like '*SCORP_NATIVE_STDERR_OK*' -and [string]$nativeResult.evidence.stdout_tail -like '*SCORP_NATIVE_STDOUT_OK*') "H2-native-stderr-captured-as-evidence"
} finally { Remove-Item -LiteralPath $nativeFixture -Recurse -Force -ErrorAction SilentlyContinue }

# Remote lifecycle evidence is accepted only from the trusted GitHub author.
$claims=Get-Slice $executor "Get-MatchingClaims" "Get-MatchingResults"
$results=Get-Slice $executor "Get-MatchingResults" "Get-MatchingBlockComments"
Assert-True ($claims.Contains("TrustedAuthor") -and $claims.Contains("user.login")) "trusted-claim-author"
Assert-True ($results.Contains("TrustedAuthor") -and $results.Contains("user.login")) "trusted-result-author"

# Windows PowerShell 5.1 does not expose .Count on a single [pscustomobject].
# Every match-set used with .Count must therefore be forced through @(...).
Assert-True ($executor.Contains('$matches=@(Get-MatchingClaims') -and $executor.Contains('$matches=@(Get-MatchingResults') -and $executor.Contains('$matches=@(Get-MatchingBlockComments') -and $executor.Contains('$matches=@(Get-MatchingRejectionComments')) "ps51-match-count-array-wrapped"
Assert-True ($executor.Contains('@(Get-MatchingResults @($trusted) 7 "task" "action-0001" "claim-0001").Count')) "ps51-selftest-match-count-array-wrapped"
Assert-True ($executor.Contains('$found=@(Find-RunnerProcesses $State)')) "ps51-runner-discovery-count-array-wrapped"

# I — bootstrap is commit-pinned, hash-manifest verified, rollback-safe, principal-safe, mutex-verified, upgrade-quiescent, and Windows PowerShell 5.1 safe.
Assert-True ($bootstrap.Contains("CommitSha") -and $bootstrap.Contains('?ref=$CommitSha')) "I-commit-pinned"
Assert-True ($bootstrap.Contains('[string]$ControlRepo = $Repo') -or $bootstrap.Contains('[string]$ControlRepo=$Repo')) "I-control-repo-defaults-to-source"
Assert-True ($bootstrap.Contains('-Repo "{1}"') -and $bootstrap.Contains('$installedExecutor,$ControlRepo')) "I-runtime-action-uses-control-repo"
Assert-True ($bootstrap.Contains('control_repo=$ControlRepo')) "I-bootstrap-evidence-binds-control-repo"
Assert-True (-not $bootstrap.Contains('/repos/$Repo/commits/$CommitSha')) "I-no-redundant-commit-endpoint"
$gh=Get-Slice $bootstrap "Invoke-GhJson" "Get-PinnedContentObject"
Assert-True ($gh.Contains("endpoint=") -and $gh.Contains("Arguments")) "I-gh-error-identifies-endpoint"
Assert-True ($gh.Contains("System.Diagnostics.ProcessStartInfo") -and $gh.Contains("RedirectStandardError") -and $gh.Contains("RedirectStandardOutput") -and -not $gh.Contains('& gh')) "I-gh-native-stderr-isolated"
$pinned=Get-Slice $bootstrap "Get-PinnedContentObject" "Get-ManifestEntry"
Assert-True ($pinned.Contains('${RepoPath}?ref=$CommitSha') -and -not $pinned.Contains('$RepoPath?ref=$CommitSha')) "I-pinned-query-variable-delimited"
Assert-True ($bootstrap.Contains("release-manifest-v4.json") -and $bootstrap.Contains("sha256")) "I-hash-manifest"
Assert-True ($bootstrap.Contains("Export-ScheduledTask") -and $bootstrap.Contains("Register-ScheduledTask")) "I-rollback-capable"
Assert-True ($bootstrap.Contains("WindowsIdentity") -and $bootstrap.Contains("LogonType")) "I-interactive-principal-verified"
$resolveSid=Get-Slice $bootstrap "Resolve-Sid" "Assert-InteractivePrincipal"
Assert-True ($resolveSid.Contains("Translate") -and $resolveSid.Contains("WindowsIdentity") -and $resolveSid.Contains("USERNAME") -and $resolveSid.Contains("COMPUTERNAME") -and $resolveSid.Contains("OrdinalIgnoreCase")) "I-principal-sid-alias-fallback"
Assert-True ($resolveSid.Contains("scheduled task principal identity could not be resolved")) "I-principal-sid-fallback-fails-closed"
Assert-True ($bootstrap.Contains("MutexProbe") -and $bootstrap.Contains("MUTEX_DENIED")) "I-production-mutex-verified"
$quiesceDiscovery=Get-Slice $bootstrap "Get-InstalledV4Processes" "Wait-V4Quiescent"
$quiesceWait=Get-Slice $bootstrap "Wait-V4Quiescent" "Assert-NoNewCodexProcess"
Assert-True ($quiesceDiscovery.Contains("Get-CimInstance") -and $quiesceDiscovery.Contains("Win32_Process") -and $quiesceDiscovery.Contains("ErrorAction Stop") -and $quiesceDiscovery.Contains("executor-v4.1.ps1") -and $quiesceDiscovery.Contains("runner-v4.1.ps1")) "I-upgrade-quiescence-authoritative-process-discovery"
Assert-True ($quiesceWait.Contains("Start-Sleep") -and $quiesceWait.Contains("old V4 processes did not exit") -and $quiesceWait.Contains("Get-InstalledV4Processes")) "I-upgrade-quiescence-timeout-fails-closed"
$stopIndex=$bootstrap.IndexOf('Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue',[StringComparison]::Ordinal)
$waitIndex=$bootstrap.IndexOf('Wait-V4Quiescent -Root $InstallDir',[StringComparison]::Ordinal)
$moveIndex=$bootstrap.IndexOf('Move-Item -LiteralPath $InstallDir -Destination $BackupDir',[StringComparison]::Ordinal)
Assert-True ($stopIndex-ge0 -and $waitIndex-gt$stopIndex -and $moveIndex-gt$waitIndex) "I-upgrade-quiescence-before-install-swap"
Assert-True (-not ($bootstrap -match '\(\s*if\s*\(')) "I-no-parenthesized-if-expression"
Assert-True ($bootstrap.Contains('$rollbackDirValue=$null') -and $bootstrap.Contains('if($priorInstallExisted){$rollbackDirValue=$BackupDir}') -and $bootstrap.Contains('-NotePropertyValue $rollbackDirValue')) "I-rollback-dir-ps51-runtime-safe"
$ambiguousColon='\$(?!(?:env|global|script|local|private|using):)[A-Za-z_][A-Za-z0-9_]*:'
Assert-True ('$pidValue:' -match $ambiguousColon) "I-ambiguous-variable-colon-regex-positive-control"
Assert-True ('$env:COMPUTERNAME' -notmatch $ambiguousColon -and '$global:LASTEXITCODE' -notmatch $ambiguousColon -and '$script:value' -notmatch $ambiguousColon) "I-valid-scoped-variable-colon-regex-negative-control"
Assert-True (-not ($executor -match $ambiguousColon)) "I-executor-no-ambiguous-variable-colon-interpolation"
Assert-True (-not ($runner -match $ambiguousColon)) "I-runner-no-ambiguous-variable-colon-interpolation"
Assert-True (-not ($bootstrap -match $ambiguousColon)) "I-bootstrap-no-ambiguous-variable-colon-interpolation"

# J — terminal result status maps to explicit DONE / FAILED / BLOCKED titles.
$finalize=Get-Slice $executor "Finalize-State" "Block-State"
Assert-True ($executor.Contains("[SCORP_EXEC_FAILED]") -and $finalize.Contains("PRECONDITION_FAILED") -and $finalize.Contains("TIMED_OUT")) "J-failure-terminal-mapping"
Assert-True ($finalize.Contains("BlockedPrefix") -and $finalize.Contains("DonePrefix") -and $finalize.Contains("FailedPrefix")) "J-explicit-terminal-prefixes"
Assert-True ($finalize.Contains("runner_pid") -and $finalize.Contains("child_pid")) "J-result-binds-runner-pid"

# L 闁?privileged broker runner route is explicit, fixed-path, durable, and generation-bound.
$gen=Get-Slice $runner "Get-BrokerGenerationToken" "Get-DeterministicBrokerRequestId"
$rid=Get-Slice $runner "Get-DeterministicBrokerRequestId" "Get-BrokerRequestPath"
$reqPath=Get-Slice $runner "Get-BrokerRequestPath" "Invoke-PrivilegedBrokerAction"
$invokeBroker=Get-Slice $runner "Invoke-PrivilegedBrokerAction" "Add-NativeEvidence"
Assert-True ($gen.Contains('/g') -and $gen.Contains('direct')) "L-generation-token-explicit"
Assert-True ($rid.Contains('TaskId') -and $rid.Contains('ActionId') -and $rid.Contains('GenerationToken') -and $rid.Contains('SHA256')) "L-request-id-binds-task-action-generation"
Assert-True ($reqPath.Contains('C:\ScorpAgent\state-v4\broker-requests')) "L-fixed-broker-request-root"
Assert-True ($invokeBroker.Contains('C:\ScorpAgent\privileged-broker-runtime\Scripts\python.exe') -and $invokeBroker.Contains('C:\ScorpAgent\privileged-broker\broker_client.py')) "L-fixed-broker-runtime-client"
Assert-True ($invokeBroker.Contains('--request-file') -and $invokeBroker.Contains('--request-id') -and $invokeBroker.Contains('--operation') -and $invokeBroker.Contains('--params-json')) "L-runner-durable-broker-request-contract"
Assert-True (-not $invokeBroker.Contains('payload.executable') -and -not $invokeBroker.Contains('payload.script') -and -not $invokeBroker.Contains('payload.pipe') -and -not $invokeBroker.Contains('payload.secret')) "L-no-envelope-controlled-broker-transport"
Assert-True ($runner.Contains('"privileged_broker"{') -and $runner.Contains('Invoke-PrivilegedBrokerAction')) "L-runner-explicit-broker-case"
Assert-True (-not ($executor -match '(?i)codex\s+exec|codex\.exe')) "executor-no-codex-invocation"
Assert-True (-not ($runner -match '(?i)codex\s+exec|codex\.exe')) "runner-no-codex-invocation"
Assert-True (-not ($bootstrap -match '(?i)codex\s+exec|codex\.exe')) "bootstrap-no-codex-invocation"
Write-Output "V4_PRODUCTION_HARDENING_PASS"
