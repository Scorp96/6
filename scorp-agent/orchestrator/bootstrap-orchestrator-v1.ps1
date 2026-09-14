param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$CommitSha,
    [string]$Repo='Scorp96/666',
    [string]$ControlRepo=$Repo,
    [string]$TaskName='ScorpFullAutoOrchestrator',
    [string]$V4TaskName='ScorpComputerAgent',
    [string]$InstallDir='C:\ScorpAgent\full-auto-orchestrator-v1',
    [string]$StateRoot='C:\ScorpAgent\orchestrator-v1'
)

$ErrorActionPreference='Stop'
$RequiredV4Commit='4588799df1047a8ddd833e217698774c74bd29e6'
$ManifestRepoPath='scorp-agent/orchestrator/release-manifest-orchestrator-v1.json'
$WindowsPowerShell="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$EvidencePath=Join-Path $StateRoot 'bootstrap-orchestrator-v1-evidence.json'
$PriorTaskXmlPath=Join-Path $StateRoot 'bootstrap-orchestrator-v1-prior-task.xml'
$StagingDir="$InstallDir.staging.$([guid]::NewGuid().ToString('N'))"
$BackupDir="$InstallDir.rollback.$(Get-Date -Format 'yyyyMMddHHmmss')"
New-Item -ItemType Directory -Force -Path $StateRoot,$StagingDir|Out-Null

function Write-Utf8NoBom {
    param([string]$Path,[string]$Text)
    $dir=Split-Path -Parent $Path
    if($dir){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
    [IO.File]::WriteAllText($Path,$Text,(New-Object Text.UTF8Encoding($false)))
}

function Initialize-OrchestratorRegistry {
    param([string]$Root)
    $registryPath=Join-Path $Root 'registry.json'
    if(Test-Path -LiteralPath $registryPath -PathType Leaf){return}
    $walPath=Join-Path $Root 'journal.jsonl'
    if(Test-Path -LiteralPath $walPath -PathType Leaf){
        $walLength=[int64](Get-Item -LiteralPath $walPath -ErrorAction Stop).Length
        if($walLength-gt0){throw "REGISTRY_MISSING_WITH_EXISTING_WAL path=$walPath size=$walLength"}
    }
    $initial=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@();updated_at=[DateTimeOffset]::UtcNow.ToString('o')}
    Write-Utf8NoBom $registryPath ($initial|ConvertTo-Json -Depth 16)
}
function Get-Sha256Text {
    param([string]$Text)
    $sha=[Security.Cryptography.SHA256]::Create()
    try{return(([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))))-replace'-','').ToLowerInvariant()}
    finally{$sha.Dispose()}
}

function Get-Sha256File {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-GitBlobSha1 {
    param([byte[]]$Bytes)
    $prefix=[Text.Encoding]::ASCII.GetBytes("blob $($Bytes.Length)`0")
    $all=New-Object byte[] ($prefix.Length+$Bytes.Length)
    [Array]::Copy($prefix,0,$all,0,$prefix.Length)
    [Array]::Copy($Bytes,0,$all,$prefix.Length,$Bytes.Length)
    $sha=[Security.Cryptography.SHA1]::Create()
    try{return(([BitConverter]::ToString($sha.ComputeHash($all)))-replace'-','').ToLowerInvariant()}
    finally{$sha.Dispose()}
}

function Invoke-GhJson {
    param([string[]]$Arguments,[int]$TimeoutSeconds=30)
    foreach($arg in $Arguments){if([string]::IsNullOrWhiteSpace($arg)-or$arg-match'[\r\n"]'){throw "unsupported gh argument: [$arg]"}}
    $gh=(Get-Command gh.exe -CommandType Application -ErrorAction Stop).Source
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$gh
    $psi.Arguments=($Arguments -join ' ')
    $psi.UseShellExecute=$false
    $psi.CreateNoWindow=$true
    $psi.RedirectStandardOutput=$true
    $psi.RedirectStandardError=$true
    $proc=New-Object System.Diagnostics.Process
    $proc.StartInfo=$psi
    try{
        if(-not$proc.Start()){throw 'GITHUB_API_START_FAILED'}
        $outTask=$proc.StandardOutput.ReadToEndAsync()
        $errTask=$proc.StandardError.ReadToEndAsync()
        if(-not$proc.WaitForExit([Math]::Max(1,$TimeoutSeconds)*1000)){
            try{$proc.Kill()}catch{}
            try{[void]$proc.WaitForExit(2000)}catch{}
            throw "GITHUB_API_TIMEOUT timeout_seconds=$TimeoutSeconds args=$($Arguments-join' ')"
        }
        $outTask.Wait();$errTask.Wait()
        $stdout=[string]$outTask.Result;$stderr=[string]$errTask.Result;$code=[int]$proc.ExitCode
        if($code-ne0){throw "GITHUB_API_FAILED exit=$code args=$($Arguments-join' ') stderr=$stderr"}
        if([string]::IsNullOrWhiteSpace($stdout)){throw "GITHUB_API_EMPTY args=$($Arguments-join' ')"}
        return ($stdout|ConvertFrom-Json)
    }finally{$proc.Dispose()}
}

function Get-PinnedContentObject {
    param([string]$RepoPath)
    $obj=Invoke-GhJson -Arguments @('api',"/repos/$Repo/contents/${RepoPath}?ref=$CommitSha")
    if([string]$obj.encoding-cne'base64'-or[string]::IsNullOrWhiteSpace([string]$obj.content)-or[string]::IsNullOrWhiteSpace([string]$obj.sha)){throw "unexpected GitHub content response for $RepoPath"}
    $bytes=[Convert]::FromBase64String(([string]$obj.content-replace'\s',''))
    $blob=Get-GitBlobSha1 $bytes
    if($blob-cne([string]$obj.sha).ToLowerInvariant()){throw "downloaded Git blob hash mismatch for $RepoPath"}
    return [pscustomobject]@{repo_path=$RepoPath;bytes=$bytes;git_blob_sha=$blob}
}

function Get-ManifestEntry {
    param($Manifest,[string]$RepoPath)
    $prop=$Manifest.files.PSObject.Properties[$RepoPath]
    if($null-eq$prop){throw "manifest missing entry: $RepoPath"}
    return $prop.Value
}

function Install-PinnedFile {
    param($Manifest,[string]$RepoPath,[string]$Destination)
    $entry=Get-ManifestEntry $Manifest $RepoPath
    $obj=Get-PinnedContentObject $RepoPath
    $expected=([string]$entry.git_blob_sha).ToLowerInvariant()
    if($expected-cnotmatch'^[0-9a-f]{40}$'){throw "invalid manifest git_blob_sha for $RepoPath"}
    if($obj.git_blob_sha-cne$expected){throw "manifest Git blob mismatch for $RepoPath expected=$expected actual=$($obj.git_blob_sha)"}
    $dir=Split-Path -Parent $Destination
    if($dir){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
    [IO.File]::WriteAllBytes($Destination,$obj.bytes)
    return [pscustomobject]@{repo_path=$RepoPath;path=$Destination;git_blob_sha=$obj.git_blob_sha;sha256=(Get-Sha256File $Destination);size_bytes=[int64](Get-Item -LiteralPath $Destination).Length}
}

function Assert-PowerShellSyntax {
    param([string]$Path)
    $tokens=$null;$errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)|Out-Null
    if($errors.Count-gt0){throw "PowerShell syntax failed for ${Path}: $(($errors|ForEach-Object{$_.Message})-join'; ')"}
}

function Resolve-Sid {
    param([string]$Identity)
    if([string]::IsNullOrWhiteSpace($Identity)){throw 'scheduled task principal UserId is empty'}
    if($Identity-match'^S-1-'){return (New-Object Security.Principal.SecurityIdentifier($Identity)).Value}
    $current=[Security.Principal.WindowsIdentity]::GetCurrent()
    try{return (New-Object Security.Principal.NTAccount($Identity)).Translate([Security.Principal.SecurityIdentifier]).Value}
    catch{
        $aliases=@([string]$current.Name,[string]$env:USERNAME)
        if(-not[string]::IsNullOrWhiteSpace($env:COMPUTERNAME)-and-not[string]::IsNullOrWhiteSpace($env:USERNAME)){$aliases+=("{0}\{1}"-f$env:COMPUTERNAME,$env:USERNAME);$aliases+=(".\{0}"-f$env:USERNAME)}
        foreach($candidate in @($aliases|Where-Object{-not[string]::IsNullOrWhiteSpace($_)}|Select-Object -Unique)){
            if([string]::Equals($Identity,$candidate,[StringComparison]::OrdinalIgnoreCase)){return $current.User.Value}
        }
        throw "scheduled task principal identity could not be resolved identity=[$Identity] current=[$($current.Name)]"
    }
}

function Assert-InteractivePrincipal {
    param($Task)
    $current=[Security.Principal.WindowsIdentity]::GetCurrent()
    $taskSid=Resolve-Sid ([string]$Task.Principal.UserId)
    if($taskSid-cne$current.User.Value){throw "Scheduled Task principal SID mismatch task=$taskSid current=$($current.User.Value)"}
    if([string]$Task.Principal.LogonType-cne'Interactive'){throw "Scheduled Task must use Interactive logon; actual=$($Task.Principal.LogonType)"}
    return [pscustomobject]@{user_name=$current.Name;sid=$current.User.Value;logon_type=[string]$Task.Principal.LogonType}
}

function Get-V4TaskFingerprint {
    param([string]$Name=$V4TaskName)
    $task=Get-ScheduledTask -TaskName $Name -ErrorAction Stop
    $xml=Export-ScheduledTask -TaskName $Name
    return [pscustomobject]@{sha256=(Get-Sha256Text $xml);xml=$xml;state=[string]$task.State}
}

function Assert-V4Dependency {
    param([string]$ExpectedCommit)
    $path='C:\ScorpAgent\state-v4\bootstrap-v4-evidence.json'
    if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "V4_DEPENDENCY_EVIDENCE_MISSING path=$path"}
    try{$e=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json}catch{throw "V4_DEPENDENCY_EVIDENCE_INVALID $($_.Exception.Message)"}
    if([string]$e.commit_sha-cne$ExpectedCommit){throw "V4_DEPENDENCY_COMMIT_MISMATCH expected=$ExpectedCommit actual=$($e.commit_sha)"}
    $task=Get-ScheduledTask -TaskName $V4TaskName -ErrorAction Stop
    $actions=@($task.Actions)
    if($actions.Count-ne1-or[string]$actions[0].Arguments-notlike'*executor-v4.1.ps1*'){throw 'V4_DEPENDENCY_TASK_ACTION_INVALID'}
    [void](Assert-InteractivePrincipal $task)
    return $e
}

function Get-InstalledOrchestratorProcesses {
    param([string]$Root)
    $entry=Join-Path $Root 'orchestrator-v1.ps1'
    try{$all=@(Get-CimInstance Win32_Process -ErrorAction Stop)}catch{throw "cannot authoritatively inspect old orchestrator processes: $($_.Exception.Message)"}
    $out=@()
    foreach($p in $all){
        if([string]$p.Name-notin@('powershell.exe','pwsh.exe')){continue}
        $cmd=[string]$p.CommandLine
        if(-not[string]::IsNullOrWhiteSpace($cmd)-and$cmd.IndexOf($entry,[StringComparison]::OrdinalIgnoreCase)-ge0){$out+=[pscustomobject]@{pid=[int]$p.ProcessId;command_line=$cmd}}
    }
    return @($out)
}

function Wait-OrchestratorQuiescent {
    param([string]$Root,[int]$Seconds=15)
    if(-not(Test-Path -LiteralPath $Root -PathType Container)){return}
    $deadline=[DateTimeOffset]::Now.AddSeconds([Math]::Max(1,$Seconds))
    while([DateTimeOffset]::Now-lt$deadline){
        $found=@(Get-InstalledOrchestratorProcesses -Root $Root)
        if($found.Count-eq0){return}
        Start-Sleep -Milliseconds 250
    }
    $found=@(Get-InstalledOrchestratorProcesses -Root $Root)
    if($found.Count-eq0){return}
    throw "old orchestrator processes did not exit after requested task stop within $Seconds seconds: pids=$(@($found|ForEach-Object{[string]$_.pid})-join',')"
}

function Assert-NoNewCodexProcess {
    param([int[]]$BeforeIds)
    $after=@(Get-Process -Name codex -ErrorAction SilentlyContinue|ForEach-Object{[int]$_.Id})
    $new=@($after|Where-Object{$BeforeIds-notcontains$_})
    if($new.Count-gt0){throw "unexpected new Codex process during orchestrator bootstrap: $($new-join',')"}
}

function Invoke-TestScript {
    param([string]$Path,[string[]]$Arguments,[string]$PassMarker,[int]$TimeoutSeconds=120)
    foreach($arg in $Arguments){if([string]$arg-match'[\r\n"]'){throw "unsupported test argument: [$arg]"}}
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$WindowsPowerShell
    $psi.Arguments=('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File '+$Path+' '+($Arguments-join' ')).Trim()
    $psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
    $p=New-Object System.Diagnostics.Process;$p.StartInfo=$psi
    try{
        if(-not$p.Start()){throw "test process failed to start: $Path"}
        $outTask=$p.StandardOutput.ReadToEndAsync();$errTask=$p.StandardError.ReadToEndAsync()
        if(-not$p.WaitForExit([Math]::Max(1,$TimeoutSeconds)*1000)){try{$p.Kill()}catch{};throw "test timeout: $Path"}
        $outTask.Wait();$errTask.Wait();$out=[string]$outTask.Result;$err=[string]$errTask.Result;$code=[int]$p.ExitCode
        if($code-ne0-or$out-notmatch[regex]::Escape($PassMarker)){throw "test failed path=$Path exit=$code stdout=$out stderr=$err"}
        return $out
    }finally{$p.Dispose()}
}

$taskMutated=$false
$installMoved=$false
$priorInstallExisted=$false
$priorTask=$null
$priorTaskXml=$null
$priorTaskState=$null
$rollbackError=$null
$codexBefore=@(Get-Process -Name codex -ErrorAction SilentlyContinue|ForEach-Object{[int]$_.Id})

try{
    if(-not(Test-Path -LiteralPath $WindowsPowerShell -PathType Leaf)){throw 'Windows PowerShell 5.1 executable missing'}
    if(-not(Get-Command gh.exe -ErrorAction SilentlyContinue)){throw 'gh.exe is required'}

    $v4Evidence=Assert-V4Dependency -ExpectedCommit $RequiredV4Commit
    $v4Before=Get-V4TaskFingerprint

    $priorTask=Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if($null-ne$priorTask){
        $priorPrincipal=Assert-InteractivePrincipal $priorTask
        $priorActions=@($priorTask.Actions)
        $installedEntry=Join-Path $InstallDir 'orchestrator-v1.ps1'
        if($priorActions.Count-ne1-or[string]$priorActions[0].Execute-cne$WindowsPowerShell-or[string]$priorActions[0].Arguments-notlike("*"+$installedEntry+"*")){throw 'INCOMPATIBLE_ORCHESTRATOR_TASK existing action/principal is not owned by orchestrator-v1'}
        $priorTaskXml=Export-ScheduledTask -TaskName $TaskName
        Write-Utf8NoBom $PriorTaskXmlPath $priorTaskXml
        $priorTaskState=[string]$priorTask.State
    }elseif(Test-Path -LiteralPath $InstallDir -PathType Container){throw 'INCOMPATIBLE_ORCHESTRATOR_TASK install directory exists without owned Scheduled Task'}

    $manifestObj=Get-PinnedContentObject $ManifestRepoPath
    $manifestText=[Text.Encoding]::UTF8.GetString($manifestObj.bytes)
    $manifest=$manifestText|ConvertFrom-Json
    if([string]$manifest.protocol_version-cne'scorp.orchestrator/release-manifest-v1'){throw 'unsupported orchestrator release manifest protocol'}
    if([string]$manifest.executor_dependency_commit-cne$RequiredV4Commit){throw 'orchestrator manifest V4 dependency mismatch'}
    $manifestLocal=Join-Path $StagingDir 'release-manifest-orchestrator-v1.json'
    [IO.File]::WriteAllBytes($manifestLocal,$manifestObj.bytes)

    $paths=[ordered]@{
        'scorp-agent/orchestrator-v1.ps1'=(Join-Path $StagingDir 'orchestrator-v1.ps1')
        'scorp-agent/orchestrator/store-v1.ps1'=(Join-Path $StagingDir 'orchestrator\store-v1.ps1')
        'scorp-agent/orchestrator/registry-v1.ps1'=(Join-Path $StagingDir 'orchestrator\registry-v1.ps1')
        'scorp-agent/orchestrator/health-control-v1.ps1'=(Join-Path $StagingDir 'orchestrator\health-control-v1.ps1')
        'scorp-agent/orchestrator/router-v4.ps1'=(Join-Path $StagingDir 'orchestrator\router-v4.ps1')
        'scorp-agent/orchestrator/continuation-v1.ps1'=(Join-Path $StagingDir 'orchestrator\continuation-v1.ps1')
        'scorp-agent/orchestrator/task-schema-v1.json'=(Join-Path $StagingDir 'orchestrator\task-schema-v1.json')
        'scorp-agent/orchestrator/continuation-schema-v1.json'=(Join-Path $StagingDir 'orchestrator\continuation-schema-v1.json')
        'scorp-agent/orchestrator/bootstrap-orchestrator-v1.ps1'=(Join-Path $StagingDir 'bootstrap-orchestrator-v1.ps1')
        'scorp-agent/tests/orchestrator-v1-contract-store.ps1'=(Join-Path $StagingDir 'tests\orchestrator-v1-contract-store.ps1')
        'scorp-agent/tests/orchestrator-v1-scheduler-control.ps1'=(Join-Path $StagingDir 'tests\orchestrator-v1-scheduler-control.ps1')
        'scorp-agent/tests/orchestrator-v1-router-continuation.ps1'=(Join-Path $StagingDir 'tests\orchestrator-v1-router-continuation.ps1')
        'scorp-agent/tests/orchestrator-v1-recovery-deployment.ps1'=(Join-Path $StagingDir 'tests\orchestrator-v1-recovery-deployment.ps1')
        'scorp-agent/tests/orchestrator-v1-production-wiring.ps1'=(Join-Path $StagingDir 'tests\orchestrator-v1-production-wiring.ps1')
        'scorp-agent/tests/orchestrator-v1-deployment-regression.ps1'=(Join-Path $StagingDir 'tests\orchestrator-v1-deployment-regression.ps1')
    }
    $artifacts=@()
    foreach($repoPath in $paths.Keys){$artifacts+=Install-PinnedFile -Manifest $manifest -RepoPath $repoPath -Destination $paths[$repoPath]}

    foreach($ps1 in @($paths.Values|Where-Object{[string]$_ -like'*.ps1'})){Assert-PowerShellSyntax $ps1}
    foreach($json in @($paths['scorp-agent/orchestrator/task-schema-v1.json'],$paths['scorp-agent/orchestrator/continuation-schema-v1.json'],$manifestLocal)){[void](Get-Content -LiteralPath $json -Raw|ConvertFrom-Json)}

    $contractText=Invoke-TestScript -Path $paths['scorp-agent/tests/orchestrator-v1-contract-store.ps1'] -Arguments @('-TaskSchemaPath',$paths['scorp-agent/orchestrator/task-schema-v1.json'],'-ContinuationSchemaPath',$paths['scorp-agent/orchestrator/continuation-schema-v1.json'],'-StorePath',$paths['scorp-agent/orchestrator/store-v1.ps1']) -PassMarker 'ORCHESTRATOR_V1_CONTRACT_STORE_PASS'
    $schedulerText=Invoke-TestScript -Path $paths['scorp-agent/tests/orchestrator-v1-scheduler-control.ps1'] -Arguments @('-StorePath',$paths['scorp-agent/orchestrator/store-v1.ps1'],'-RegistryPath',$paths['scorp-agent/orchestrator/registry-v1.ps1'],'-HealthPath',$paths['scorp-agent/orchestrator/health-control-v1.ps1'],'-EntrypointPath',$paths['scorp-agent/orchestrator-v1.ps1']) -PassMarker 'ORCHESTRATOR_V1_SCHEDULER_CONTROL_PASS'
    $routerText=Invoke-TestScript -Path $paths['scorp-agent/tests/orchestrator-v1-router-continuation.ps1'] -Arguments @('-StorePath',$paths['scorp-agent/orchestrator/store-v1.ps1'],'-RouterPath',$paths['scorp-agent/orchestrator/router-v4.ps1'],'-ContinuationPath',$paths['scorp-agent/orchestrator/continuation-v1.ps1']) -PassMarker 'ORCHESTRATOR_V1_ROUTER_CONTINUATION_PASS'
    $stateMachineText=Invoke-TestScript -Path $paths['scorp-agent/tests/orchestrator-v1-recovery-deployment.ps1'] -Arguments @('-StorePath',$paths['scorp-agent/orchestrator/store-v1.ps1'],'-RegistryPath',$paths['scorp-agent/orchestrator/registry-v1.ps1'],'-HealthPath',$paths['scorp-agent/orchestrator/health-control-v1.ps1'],'-RouterPath',$paths['scorp-agent/orchestrator/router-v4.ps1'],'-ContinuationPath',$paths['scorp-agent/orchestrator/continuation-v1.ps1'],'-EntrypointPath',$paths['scorp-agent/orchestrator-v1.ps1']) -PassMarker 'ORCHESTRATOR_V1_STATE_MACHINE_RECOVERY_PASS' -TimeoutSeconds 180
    $wiringText=Invoke-TestScript -Path $paths['scorp-agent/tests/orchestrator-v1-production-wiring.ps1'] -Arguments @('-StorePath',$paths['scorp-agent/orchestrator/store-v1.ps1'],'-RouterPath',$paths['scorp-agent/orchestrator/router-v4.ps1'],'-EntrypointPath',$paths['scorp-agent/orchestrator-v1.ps1']) -PassMarker 'ORCHESTRATOR_V1_PRODUCTION_WIRING_PASS'
    $deploymentText=Invoke-TestScript -Path $paths['scorp-agent/tests/orchestrator-v1-deployment-regression.ps1'] -Arguments @('-BootstrapPath',$paths['scorp-agent/orchestrator/bootstrap-orchestrator-v1.ps1'],'-ManifestPath',$manifestLocal,'-EntrypointPath',$paths['scorp-agent/orchestrator-v1.ps1'],'-StorePath',$paths['scorp-agent/orchestrator/store-v1.ps1'],'-RegistryPath',$paths['scorp-agent/orchestrator/registry-v1.ps1'],'-HealthPath',$paths['scorp-agent/orchestrator/health-control-v1.ps1'],'-RouterPath',$paths['scorp-agent/orchestrator/router-v4.ps1'],'-ContinuationPath',$paths['scorp-agent/orchestrator/continuation-v1.ps1'],'-TaskSchemaPath',$paths['scorp-agent/orchestrator/task-schema-v1.json'],'-ContinuationSchemaPath',$paths['scorp-agent/orchestrator/continuation-schema-v1.json']) -PassMarker 'ORCHESTRATOR_V1_DEPLOYMENT_REGRESSION_PASS'
    Assert-NoNewCodexProcess $codexBefore

    $pre=[ordered]@{protocol_version='scorp.orchestrator/bootstrap-evidence-v1';commit_sha=$CommitSha.ToLowerInvariant();repo=$Repo;control_repo=$ControlRepo;task_name=$TaskName;v4_task_name=$V4TaskName;started_at=(Get-Date -Format o);phase='PRE_SWITCH_VERIFIED';v4_dependency_commit=$RequiredV4Commit;v4_task_fingerprint_before=$v4Before.sha256;manifest_git_blob_sha=$manifestObj.git_blob_sha;artifacts=$artifacts;contract_test=$contractText;scheduler_test=$schedulerText;router_continuation_test=$routerText;state_machine_test=$stateMachineText;wiring_test=$wiringText;deployment_test=$deploymentText;rollback_performed=$false;rollback_error=$null}
    Write-Utf8NoBom $EvidencePath ($pre|ConvertTo-Json -Depth 32)

    if($null-ne$priorTask){
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $taskMutated=$true
        Wait-OrchestratorQuiescent -Root $InstallDir -Seconds 15
    }
    if(Test-Path -LiteralPath $InstallDir -PathType Container){$priorInstallExisted=$true;Move-Item -LiteralPath $InstallDir -Destination $BackupDir}
    Move-Item -LiteralPath $StagingDir -Destination $InstallDir
    $installMoved=$true

    $installedEntry=Join-Path $InstallDir 'orchestrator-v1.ps1'
    Initialize-OrchestratorRegistry -Root $StateRoot
    $action=New-ScheduledTaskAction -Execute $WindowsPowerShell -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Root "{1}" -RepoFullName "{2}" -TrustedActor "{3}"' -f$installedEntry,$StateRoot,$ControlRepo,'Scorp96') -WorkingDirectory $InstallDir
    if($null-ne$priorTask){
        Set-ScheduledTask -TaskName $TaskName -Action $action|Out-Null
    }else{
        $taskMutated=$true
        $current=[Security.Principal.WindowsIdentity]::GetCurrent()
        $principal=New-ScheduledTaskPrincipal -UserId $current.Name -LogonType Interactive -RunLevel Limited
        $trigger=New-ScheduledTaskTrigger -AtLogOn -User $current.Name
        $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal|Out-Null
    }
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3

    $after=Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $afterPrincipal=Assert-InteractivePrincipal $after
    $afterActions=@($after.Actions)
    if($afterActions.Count-ne1-or[string]$afterActions[0].Execute-cne$WindowsPowerShell-or[string]$afterActions[0].Arguments-notlike("*"+$installedEntry+"*")){throw 'orchestrator task action verification failed'}
    if([string]$after.State-cne'Running'){throw "orchestrator Scheduled Task is not Running: $($after.State)"}

    $probe=& $WindowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installedEntry -MutexProbe 2>&1
    $probeText=$probe-join"`n"
    if($LASTEXITCODE-ne0-or$probeText-notmatch'MUTEX_DENIED'-or$probeText-match'MUTEX_ACQUIRED'){throw "orchestrator mutex verification failed: $probeText"}

    $v4After=Get-V4TaskFingerprint
    if([string]$v4After.sha256-cne[string]$v4Before.sha256){throw "V4_TASK_CHANGED_DURING_ORCHESTRATOR_BOOTSTRAP before=$($v4Before.sha256) after=$($v4After.sha256)"}
    Assert-NoNewCodexProcess $codexBefore

    $final=Get-Content -LiteralPath $EvidencePath -Raw|ConvertFrom-Json
    $final.phase='SWITCH_VERIFIED'
    $final|Add-Member -NotePropertyName deployed_at -NotePropertyValue (Get-Date -Format o) -Force
    $final|Add-Member -NotePropertyName installed_entrypoint -NotePropertyValue $installedEntry -Force
    $final|Add-Member -NotePropertyName deployed_principal -NotePropertyValue $afterPrincipal -Force
    $final|Add-Member -NotePropertyName mutex_probe -NotePropertyValue $probeText -Force
    $final|Add-Member -NotePropertyName v4_task_fingerprint_after -NotePropertyValue $v4After.sha256 -Force
    Write-Utf8NoBom $EvidencePath ($final|ConvertTo-Json -Depth 32)

    Write-Output 'ORCHESTRATOR_V1_BOOTSTRAP_PASS'
    Write-Output "Commit: $CommitSha"
    Write-Output "Entrypoint: $installedEntry"
    Write-Output "Evidence: $EvidencePath"
}catch{
    $failure=$_.Exception.Message
    if($taskMutated-or$installMoved){
        try{
            $existing=Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if($null-ne$existing){Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue}
            Wait-OrchestratorQuiescent -Root $InstallDir -Seconds 10
            if($null-ne$priorTaskXml){Register-ScheduledTask -TaskName $TaskName -Xml $priorTaskXml -Force|Out-Null}
            elseif($null-ne$existing){Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue}
            if($installMoved-and(Test-Path -LiteralPath $InstallDir -PathType Container)){Remove-Item -LiteralPath $InstallDir -Recurse -Force}
            if($priorInstallExisted-and(Test-Path -LiteralPath $BackupDir -PathType Container)){Move-Item -LiteralPath $BackupDir -Destination $InstallDir}
            if($null-ne$priorTaskXml-and$priorTaskState-ceq'Running'){Start-ScheduledTask -TaskName $TaskName}
        }catch{$rollbackError=$_.Exception.Message}
    }
    try{
        $e=if(Test-Path -LiteralPath $EvidencePath -PathType Leaf){Get-Content -LiteralPath $EvidencePath -Raw|ConvertFrom-Json}else{[pscustomobject][ordered]@{protocol_version='scorp.orchestrator/bootstrap-evidence-v1';commit_sha=$CommitSha.ToLowerInvariant();repo=$Repo;control_repo=$ControlRepo;task_name=$TaskName;started_at=(Get-Date -Format o)}}
        $e|Add-Member -NotePropertyName phase -NotePropertyValue 'FAILED_ROLLED_BACK' -Force
        $e|Add-Member -NotePropertyName failed_at -NotePropertyValue (Get-Date -Format o) -Force
        $e|Add-Member -NotePropertyName failure -NotePropertyValue $failure -Force
        $e|Add-Member -NotePropertyName rollback_performed -NotePropertyValue ([bool]($taskMutated-or$installMoved)) -Force
        $e|Add-Member -NotePropertyName rollback_error -NotePropertyValue $rollbackError -Force
        Write-Utf8NoBom $EvidencePath ($e|ConvertTo-Json -Depth 32)
    }catch{}
    if($rollbackError){throw "Orchestrator bootstrap failed: $failure; rollback also failed: $rollbackError"}
    throw "Orchestrator bootstrap failed and rollback completed: $failure"
}finally{
    if(Test-Path -LiteralPath $StagingDir -PathType Container){Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue}
}
