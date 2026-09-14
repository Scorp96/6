param(
    [Parameter(Mandatory=$true)][string]$BootstrapPath,
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$EntrypointPath,
    [Parameter(Mandatory=$true)][string]$StorePath,
    [Parameter(Mandatory=$true)][string]$RegistryPath,
    [Parameter(Mandatory=$true)][string]$HealthPath,
    [Parameter(Mandatory=$true)][string]$RouterPath,
    [Parameter(Mandatory=$true)][string]$ContinuationPath,
    [Parameter(Mandatory=$true)][string]$TaskSchemaPath,
    [Parameter(Mandatory=$true)][string]$ContinuationSchemaPath
)

$ErrorActionPreference='Stop'
$ExpectedV4='4588799df1047a8ddd833e217698774c74bd29e6'

function Assert-True {
    param([bool]$Condition,[string]$Name)
    if(-not$Condition){throw "ORCHESTRATOR_V1_DEPLOYMENT_REGRESSION_FAIL: $Name"}
    Write-Output "PASS $Name"
}

foreach($p in @($EntrypointPath,$StorePath,$RegistryPath,$HealthPath,$RouterPath,$ContinuationPath,$TaskSchemaPath,$ContinuationSchemaPath)){
    if(-not(Test-Path -LiteralPath $p -PathType Leaf)){throw "ORCH_TASK6_BASE_ARTIFACT_MISSING: $p"}
}
if(-not(Test-Path -LiteralPath $BootstrapPath -PathType Leaf)){throw "ORCH_TASK6_BOOTSTRAP_MISSING: $BootstrapPath"}
if(-not(Test-Path -LiteralPath $ManifestPath -PathType Leaf)){throw "ORCH_TASK6_MANIFEST_MISSING: $ManifestPath"}

$bootstrap=Get-Content -LiteralPath $BootstrapPath -Raw
$manifestText=Get-Content -LiteralPath $ManifestPath -Raw
try{$manifest=$manifestText|ConvertFrom-Json}catch{throw "ORCH_TASK6_MANIFEST_INVALID_JSON: $($_.Exception.Message)"}

Assert-True ([string]$manifest.protocol_version -ceq 'scorp.orchestrator/release-manifest-v1') 'manifest-protocol'
Assert-True ([string]$manifest.executor_dependency_commit -ceq $ExpectedV4) 'manifest-current-v4-dependency'
Assert-True ([string]$manifest.install_root -ceq 'C:\ScorpAgent\full-auto-orchestrator-v1') 'manifest-install-root'
Assert-True ([string]$manifest.state_root -ceq 'C:\ScorpAgent\orchestrator-v1') 'manifest-state-root'

$required=@(
    'scorp-agent/orchestrator-v1.ps1',
    'scorp-agent/orchestrator/store-v1.ps1',
    'scorp-agent/orchestrator/registry-v1.ps1',
    'scorp-agent/orchestrator/health-control-v1.ps1',
    'scorp-agent/orchestrator/router-v4.ps1',
    'scorp-agent/orchestrator/continuation-v1.ps1',
    'scorp-agent/orchestrator/task-schema-v1.json',
    'scorp-agent/orchestrator/continuation-schema-v1.json'
)
foreach($repoPath in $required){
    $prop=$manifest.files.PSObject.Properties[$repoPath]
    Assert-True ($null-ne$prop) ('manifest-entry-'+($repoPath-replace'[^A-Za-z0-9]','-'))
    Assert-True ([string]$prop.Value.git_blob_sha -cmatch '^[0-9a-f]{40}$') ('manifest-blob-'+($repoPath-replace'[^A-Za-z0-9]','-'))
}

Assert-True ($bootstrap.Contains("[ValidatePattern('^[0-9a-fA-F]{40}$')][string]`$CommitSha")) 'commit-sha-required-validated'
Assert-True ($bootstrap.Contains('[string]$ControlRepo=$Repo') -or $bootstrap.Contains('[string]$ControlRepo = $Repo')) 'control-repo-defaults-to-source'
Assert-True ($bootstrap.Contains('-RepoFullName "{2}"') -and $bootstrap.Contains('$installedEntry,$StateRoot,$ControlRepo')) 'runtime-action-uses-control-repo'
Assert-True ($bootstrap.Contains('control_repo=$ControlRepo')) 'bootstrap-evidence-binds-control-repo'
Assert-True ($bootstrap.Contains('${RepoPath}?ref=$CommitSha')) 'all-content-fetches-commit-pinned'
Assert-True ($bootstrap.Contains('Get-GitBlobSha1') -and $bootstrap.Contains('manifest Git blob mismatch')) 'manifest-git-blob-verified'
Assert-True ($bootstrap.Contains('[System.Management.Automation.Language.Parser]::ParseFile')) 'winps-parser-gate'
Assert-True ($bootstrap.Contains('ScorpFullAutoOrchestrator')) 'separate-orchestrator-task-name'
Assert-True ($bootstrap.Contains('ScorpComputerAgent')) 'v4-task-snapshot-required'
Assert-True ($bootstrap.Contains('Export-ScheduledTask')) 'prior-task-captured-for-rollback'
Assert-True ($bootstrap.Contains('Register-ScheduledTask')) 'rollback-task-restore-capable'
Assert-True ($bootstrap.Contains('Assert-InteractivePrincipal')) 'interactive-principal-verified'
Assert-True ($bootstrap.Contains('INCOMPATIBLE_ORCHESTRATOR_TASK')) 'incompatible-existing-task-fails-closed'
Assert-True ($bootstrap.Contains('orchestrator-v1.ps1') -and -not$bootstrap.Contains('executor-v4.1.ps1" -f')) 'task-action-orchestrator-only'
Assert-True ($bootstrap.Contains('Get-V4TaskFingerprint') -and $bootstrap.Contains('V4_TASK_CHANGED_DURING_ORCHESTRATOR_BOOTSTRAP')) 'v4-task-unchanged-verified'
Assert-True ($bootstrap.Contains('Assert-V4Dependency') -and $bootstrap.Contains($ExpectedV4)) 'installed-v4-dependency-verified'
Assert-True ($bootstrap.Contains('MutexProbe') -and $bootstrap.Contains('MUTEX_DENIED')) 'post-start-mutex-denial'
Assert-True ($bootstrap.Contains('FAILED_ROLLED_BACK') -and $bootstrap.Contains('rollback_performed')) 'rollback-evidence'
Assert-True ($bootstrap.Contains('Assert-NoNewCodexProcess')) 'zero-new-codex-check'
Assert-True (-not($bootstrap -match '(?i)codex\s+exec|codex\.exe')) 'bootstrap-no-codex-invocation'

$ghStart=$bootstrap.IndexOf('function Invoke-GhJson',[StringComparison]::Ordinal)
$ghEnd=$bootstrap.IndexOf('function Get-PinnedContentObject',$ghStart,[StringComparison]::Ordinal)
Assert-True ($ghStart-ge0 -and $ghEnd-gt$ghStart) 'bootstrap-github-transport-slice'
$gh=$bootstrap.Substring($ghStart,$ghEnd-$ghStart)
Assert-True ($gh.Contains('System.Diagnostics.ProcessStartInfo') -and $gh.Contains('RedirectStandardOutput') -and $gh.Contains('RedirectStandardError')) 'bootstrap-github-native-stdio-isolated'
Assert-True ($gh.Contains('WaitForExit(') -and $gh.Contains('Kill()') -and $gh.Contains('GITHUB_API_TIMEOUT')) 'bootstrap-github-hard-timeout'

Assert-True ($bootstrap.Contains('Get-InstalledOrchestratorProcesses') -and $bootstrap.Contains('Wait-OrchestratorQuiescent')) 'upgrade-quiescence-functions'
$stopIndex=$bootstrap.IndexOf('Stop-ScheduledTask -TaskName $TaskName',[StringComparison]::Ordinal)
$waitIndex=$bootstrap.IndexOf('Wait-OrchestratorQuiescent -Root $InstallDir',[StringComparison]::Ordinal)
$moveIndex=$bootstrap.IndexOf('Move-Item -LiteralPath $InstallDir -Destination $BackupDir',[StringComparison]::Ordinal)
Assert-True ($stopIndex-ge0 -and $waitIndex-gt$stopIndex -and $moveIndex-gt$waitIndex) 'upgrade-quiescence-before-install-swap'
Assert-True ($bootstrap.Contains('old orchestrator processes did not exit')) 'upgrade-quiescence-timeout-fails-closed'

Assert-True ($bootstrap.Contains('function Initialize-OrchestratorRegistry')) 'first-run-registry-init-function'
Assert-True ($bootstrap.Contains('REGISTRY_MISSING_WITH_EXISTING_WAL')) 'missing-registry-with-wal-fails-closed'
Assert-True ($bootstrap.Contains("if(Test-Path -LiteralPath `$registryPath -PathType Leaf){return}")) 'existing-registry-preserved'
$registryInitCall=$bootstrap.IndexOf('Initialize-OrchestratorRegistry -Root $StateRoot',[StringComparison]::Ordinal)
$taskStartCall=$bootstrap.IndexOf('Start-ScheduledTask -TaskName $TaskName',[StringComparison]::Ordinal)
Assert-True ($registryInitCall-ge0 -and $taskStartCall-gt$registryInitCall) 'registry-initialized-before-task-start'
$mutationTokens=@('Register-ScheduledTask','Set-ScheduledTask','Unregister-ScheduledTask','Stop-ScheduledTask','Start-ScheduledTask')
$firstMutation=[int]::MaxValue
foreach($token in $mutationTokens){$i=$bootstrap.IndexOf($token,[StringComparison]::Ordinal);if($i-ge0-and$i-lt$firstMutation){$firstMutation=$i}}
$parserCall=$bootstrap.LastIndexOf('Assert-PowerShellSyntax',[StringComparison]::Ordinal)
$regressionCall=$bootstrap.IndexOf('ORCHESTRATOR_V1_STATE_MACHINE_RECOVERY_PASS',[StringComparison]::Ordinal)
Assert-True ($firstMutation-ne[int]::MaxValue -and $parserCall-ge0 -and $parserCall-lt$firstMutation) 'all-parser-gates-before-task-mutation'
Assert-True ($regressionCall-ge0 -and $regressionCall-lt$firstMutation) 'state-machine-regression-before-task-mutation'

foreach($ps1 in @($BootstrapPath,$EntrypointPath,$StorePath,$RegistryPath,$HealthPath,$RouterPath,$ContinuationPath)){
    $tokens=$null;$errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile($ps1,[ref]$tokens,[ref]$errors)|Out-Null
    Assert-True ($errors.Count-eq0) ('parse-'+[IO.Path]::GetFileName($ps1))
}
foreach($json in @($TaskSchemaPath,$ContinuationSchemaPath,$ManifestPath)){
    try{$null=Get-Content -LiteralPath $json -Raw|ConvertFrom-Json;$ok=$true}catch{$ok=$false}
    Assert-True $ok ('json-'+[IO.Path]::GetFileName($json))
}

Write-Output 'ORCHESTRATOR_V1_DEPLOYMENT_REGRESSION_PASS'
