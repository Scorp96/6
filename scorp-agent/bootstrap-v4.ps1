param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$CommitSha,
    [string]$Repo = "Scorp96/666",
    [string]$ControlRepo = $Repo,
    [string]$TaskName = "ScorpComputerAgent",
    [string]$InstallDir = "C:\ScorpAgent\gpt-native-v4"
)

$ErrorActionPreference = "Stop"
$StateDir = "C:\ScorpAgent\state-v4"
$EvidencePath = Join-Path $StateDir "bootstrap-v4-evidence.json"
$PriorTaskXmlPath = Join-Path $StateDir "bootstrap-v4-prior-task.xml"
$ActiveTaskPath = Join-Path $StateDir "active-task.json"
$StagingDir = "$InstallDir.staging.$([guid]::NewGuid().ToString('N'))"
$BackupDir = "$InstallDir.rollback.$(Get-Date -Format 'yyyyMMddHHmmss')"
$ManifestRepoPath = "scorp-agent/release-manifest-v4.json"
$WindowsPowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
New-Item -ItemType Directory -Force -Path $StateDir,$StagingDir | Out-Null

function Write-Utf8NoBom {
    param([string]$Path,[string]$Text)
    $dir=Split-Path -Parent $Path;if($dir){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
    [IO.File]::WriteAllText($Path,$Text,(New-Object Text.UTF8Encoding($false)))
}

function Get-Sha256 {
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
    try{return(([BitConverter]::ToString($sha.ComputeHash($all)))-replace'-','').ToLowerInvariant()}finally{$sha.Dispose()}
}

function Invoke-GhJson {
    param([string[]]$Arguments)
    $endpoint=$Arguments -join ' '
    foreach($arg in $Arguments){if([string]::IsNullOrWhiteSpace($arg) -or $arg-match'[\r\n"]'){throw "unsupported gh argument endpoint=[$endpoint]"}}
    $ghPath=(Get-Command gh.exe -ErrorAction Stop).Source
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$ghPath
    $psi.Arguments=($Arguments -join ' ')
    $psi.UseShellExecute=$false
    $psi.RedirectStandardOutput=$true
    $psi.RedirectStandardError=$true
    $psi.CreateNoWindow=$true
    $proc=New-Object System.Diagnostics.Process
    $proc.StartInfo=$psi
    try{
        if(-not $proc.Start()){throw "gh process failed to start endpoint=[$endpoint]"}
        $stdout=$proc.StandardOutput.ReadToEnd()
        $stderr=$proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        $code=$proc.ExitCode
    }finally{$proc.Dispose()}
    if($code-ne0){throw "gh failed ($code) endpoint=[$endpoint] output=$stderr"}
    if([string]::IsNullOrWhiteSpace($stdout)){throw "gh returned empty JSON endpoint=[$endpoint]"}
    return ($stdout|ConvertFrom-Json)
}

function Get-PinnedContentObject {
    param([string]$RepoPath)
    $obj=Invoke-GhJson @("api","/repos/$Repo/contents/${RepoPath}?ref=$CommitSha")
    if([string]$obj.encoding-cne"base64" -or [string]::IsNullOrWhiteSpace([string]$obj.content) -or [string]::IsNullOrWhiteSpace([string]$obj.sha)){throw "unexpected GitHub content response for $RepoPath"}
    $bytes=[Convert]::FromBase64String(([string]$obj.content-replace'\s',''))
    $localBlob=Get-GitBlobSha1 $bytes
    if($localBlob-cne([string]$obj.sha).ToLowerInvariant()){throw "downloaded Git blob hash mismatch for $RepoPath"}
    return [pscustomobject]@{repo_path=$RepoPath;bytes=$bytes;git_blob_sha=$localBlob;api_blob_sha=([string]$obj.sha).ToLowerInvariant()}
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
    if($expected-notmatch'^[0-9a-f]{40}$'){throw "invalid manifest git_blob_sha for $RepoPath"}
    if($obj.git_blob_sha-cne$expected){throw "manifest Git blob mismatch for $RepoPath expected=$expected actual=$($obj.git_blob_sha)"}
    $dir=Split-Path -Parent $Destination;if($dir){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
    [IO.File]::WriteAllBytes($Destination,$obj.bytes)
    $sha256=Get-Sha256 $Destination
    if([string]$entry.sha256 -and ([string]$entry.sha256)-notmatch'^computed-at-bootstrap$' -and $sha256-cne([string]$entry.sha256).ToLowerInvariant()){throw "manifest sha256 mismatch for $RepoPath"}
    return [pscustomobject]@{repo_path=$RepoPath;path=$Destination;git_blob_sha=$obj.git_blob_sha;sha256=$sha256;size_bytes=[int64](Get-Item -LiteralPath $Destination).Length}
}

function Assert-PowerShellSyntax {
    param([string]$Path)
    $tokens=$null;$errors=$null
    [System.Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)|Out-Null
    if($errors.Count-gt0){throw "PowerShell syntax failed for ${Path}: $(($errors|ForEach-Object{$_.Message})-join'; ')"}
}

function Resolve-Sid {
    param([string]$Identity)
    if([string]::IsNullOrWhiteSpace($Identity)){throw "scheduled task principal UserId is empty"}
    if($Identity-match'^S-1-'){return (New-Object Security.Principal.SecurityIdentifier($Identity)).Value}
    $acct=New-Object Security.Principal.NTAccount($Identity)
    try{return ($acct.Translate([Security.Principal.SecurityIdentifier])).Value}
    catch{
        $mappingError=$_.Exception.Message
        $current=[Security.Principal.WindowsIdentity]::GetCurrent()
        $aliases=@()
        if(-not[string]::IsNullOrWhiteSpace([string]$current.Name)){$aliases+=[string]$current.Name}
        if(-not[string]::IsNullOrWhiteSpace($env:USERNAME)){
            $aliases+=$env:USERNAME
            if(-not[string]::IsNullOrWhiteSpace($env:COMPUTERNAME)){$aliases+=("{0}\{1}"-f$env:COMPUTERNAME,$env:USERNAME)}
            $aliases+=(".\{0}"-f$env:USERNAME)
        }
        $slash=([string]$current.Name).LastIndexOf('\')
        if($slash-ge0 -and $slash-lt(([string]$current.Name).Length-1)){$aliases+=([string]$current.Name).Substring($slash+1)}
        foreach($candidate in @($aliases|Select-Object -Unique)){
            if([string]::Equals($Identity,[string]$candidate,[StringComparison]::OrdinalIgnoreCase)){return $current.User.Value}
        }
        throw "scheduled task principal identity could not be resolved identity=[$Identity] current=[$($current.Name)] mapping_error=$mappingError"
    }
}

function Assert-InteractivePrincipal {
    param($Task)
    $current=[Security.Principal.WindowsIdentity]::GetCurrent()
    $currentSid=$current.User.Value
    $taskSid=Resolve-Sid ([string]$Task.Principal.UserId)
    $logonType=[string]$Task.Principal.LogonType
    if($taskSid-cne$currentSid){throw "Scheduled Task principal SID mismatch task=$taskSid current=$currentSid"}
    if($logonType-cne"Interactive"){throw "Scheduled Task must use Interactive logon; actual=$logonType"}
    return [pscustomobject]@{user_name=$current.Name;sid=$currentSid;logon_type=$logonType}
}

function Invoke-TestScript {
    param([string]$Path,[string[]]$Arguments,[string]$PassMarker)
    $out=& $WindowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Path @Arguments 2>&1
    $code=$LASTEXITCODE;$text=$out-join"`n"
    if($code-ne0 -or $text-notmatch[regex]::Escape($PassMarker)){throw "test failed path=$Path exit=$code output=$text"}
    return $text
}

function Get-InstalledV4Processes {
    param([string]$Root)
    if([string]::IsNullOrWhiteSpace($Root)){throw "install root is empty"}
    $executor=Join-Path $Root "executor-v4.1.ps1"
    $runner=Join-Path $Root "runner-v4.1.ps1"
    try{$all=@(Get-CimInstance Win32_Process -ErrorAction Stop)}
    catch{throw "cannot authoritatively inspect old V4 processes: $($_.Exception.Message)"}
    $out=@()
    foreach($p in $all){
        $name=[string]$p.Name
        if($name-notin@("powershell.exe","pwsh.exe")){continue}
        $cmd=[string]$p.CommandLine
        if([string]::IsNullOrWhiteSpace($cmd)){continue}
        if($cmd.IndexOf($executor,[StringComparison]::OrdinalIgnoreCase)-ge0 -or $cmd.IndexOf($runner,[StringComparison]::OrdinalIgnoreCase)-ge0){
            $out+=[pscustomobject]@{pid=[int]$p.ProcessId;name=$name;command_line=$cmd}
        }
    }
    return @($out)
}

function Wait-V4Quiescent {
    param([string]$Root,[int]$Seconds=15)
    if(-not(Test-Path -LiteralPath $Root -PathType Container)){return}
    $deadline=[DateTimeOffset]::Now.AddSeconds([Math]::Max(1,$Seconds))
    while([DateTimeOffset]::Now-lt$deadline){
        $found=@(Get-InstalledV4Processes -Root $Root)
        if($found.Count-eq0){return}
        Start-Sleep -Milliseconds 250
    }
    $found=@(Get-InstalledV4Processes -Root $Root)
    if($found.Count-eq0){return}
    $ids=@($found|ForEach-Object{[string]$_.pid})
    throw "old V4 processes did not exit after Stop-ScheduledTask within $Seconds seconds: pids=$($ids-join',')"
}

function Assert-NoNewCodexProcess {
    param([int[]]$BeforeIds)
    $after=@(Get-Process -Name codex -ErrorAction SilentlyContinue|ForEach-Object{[int]$_.Id})
    $new=@($after|Where-Object{$BeforeIds-notcontains$_})
    if($new.Count-gt0){throw "unexpected new Codex process detected during V4 bootstrap: $($new-join',')"}
}

$taskMutated=$false
$installMoved=$false
$priorInstallExisted=$false
$priorXml=$null
$priorState=$null
$manifest=$null
$artifacts=@()
$codexBefore=@(Get-Process -Name codex -ErrorAction SilentlyContinue|ForEach-Object{[int]$_.Id})

try {
    if(Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf){throw "refusing deployment while V4 active-task.json exists"}
    if(-not(Get-Command gh.exe -ErrorAction SilentlyContinue)){throw "gh.exe is required"}
    if(-not(Test-Path -LiteralPath $WindowsPowerShell -PathType Leaf)){throw "Windows PowerShell 5.1 executable missing"}

    $manifestObj=Get-PinnedContentObject $ManifestRepoPath
    $manifestText=[Text.Encoding]::UTF8.GetString($manifestObj.bytes)
    $manifest=$manifestText|ConvertFrom-Json
    if([string]$manifest.protocol_version-cne"scorp.exec/v4-release-manifest"){throw "unsupported release manifest protocol"}

    $paths=[ordered]@{
        "scorp-agent/executor-v4.1.ps1"=(Join-Path $StagingDir "executor-v4.1.ps1")
        "scorp-agent/runner-v4.1.ps1"=(Join-Path $StagingDir "runner-v4.1.ps1")
        "scorp-agent/executor-v4.schema.json"=(Join-Path $StagingDir "executor-v4.schema.json")
        "scorp-agent/bootstrap-v4.ps1"=(Join-Path $StagingDir "bootstrap-v4.ps1")
        "scorp-agent/tests/v4-critical-hardening.ps1"=(Join-Path $StagingDir "tests\v4-critical-hardening.ps1")
        "scorp-agent/tests/v4-production-hardening.ps1"=(Join-Path $StagingDir "tests\v4-production-hardening.ps1")
        "scorp-agent/tests/v4-selfheal-hardening.ps1"=(Join-Path $StagingDir "tests\v4-selfheal-hardening.ps1")
    }
    foreach($repoPath in $paths.Keys){$artifacts+=Install-PinnedFile $manifest $repoPath $paths[$repoPath]}

    $executorPath=$paths["scorp-agent/executor-v4.1.ps1"]
    $runnerPath=$paths["scorp-agent/runner-v4.1.ps1"]
    $schemaPath=$paths["scorp-agent/executor-v4.schema.json"]
    $candidateBootstrap=$paths["scorp-agent/bootstrap-v4.ps1"]
    $criticalTest=$paths["scorp-agent/tests/v4-critical-hardening.ps1"]
    $productionTest=$paths["scorp-agent/tests/v4-production-hardening.ps1"]
    $selfhealTest=$paths["scorp-agent/tests/v4-selfheal-hardening.ps1"]

    foreach($ps1 in @($executorPath,$runnerPath,$candidateBootstrap,$criticalTest,$productionTest,$selfhealTest)){Assert-PowerShellSyntax $ps1}
    $null=Get-Content -LiteralPath $schemaPath -Raw|ConvertFrom-Json

    $selfTest=& $WindowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $executorPath -SelfTest -MutexName ("Local\ScorpV4BootstrapSelfTest-"+[guid]::NewGuid().ToString("N")) 2>&1
    $selfCode=$LASTEXITCODE;$selfText=$selfTest-join"`n"
    if($selfCode-ne0 -or $selfText-notmatch'SELFTEST PASS'){throw "V4 executor self-test failed exit=$selfCode output=$selfText"}

    $criticalText=Invoke-TestScript $criticalTest @("-ExecutorPath",$executorPath) "V4_CRITICAL_HARDENING_PASS"
    $productionText=Invoke-TestScript $productionTest @("-ExecutorPath",$executorPath,"-RunnerPath",$runnerPath,"-BootstrapPath",$candidateBootstrap) "V4_PRODUCTION_HARDENING_PASS"
    $selfhealText=Invoke-TestScript $selfhealTest @("-BootstrapPath",$candidateBootstrap) "V4_SELFHEAL_HARDENING_PASS"

    $healthId="bootstrap-health-"+[guid]::NewGuid().ToString("N")
    $claimId="bootstrap-claim-"+[guid]::NewGuid().ToString("N")
    $healthEnvelope=Join-Path $StagingDir "bootstrap-health-envelope.json"
    $healthResult=Join-Path $StagingDir "bootstrap-health-result.json"
    $healthLog=Join-Path $StagingDir "bootstrap-health-runner.log"
    $health=[ordered]@{protocol_version="scorp.exec/v4";task_id="bootstrap-health";issue_number=1;claim_token=$claimId;action_id=$healthId;action_kind="health";timeout_seconds=30;safety_class="standard";authorization=$null;payload=[ordered]@{}}
    Write-Utf8NoBom $healthEnvelope ($health|ConvertTo-Json -Depth 10)
    $healthOut=& $WindowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $runnerPath -EnvelopePath $healthEnvelope -ResultPath $healthResult -LogPath $healthLog 2>&1
    $healthCode=$LASTEXITCODE
    if($healthCode-ne0 -or -not(Test-Path -LiteralPath $healthResult)){throw "runner health smoke failed exit=$healthCode output=$($healthOut-join' ')"}
    $healthParsed=Get-Content -LiteralPath $healthResult -Raw|ConvertFrom-Json
    if([string]$healthParsed.status-cne"SUCCEEDED" -or [string]$healthParsed.task_id-cne"bootstrap-health" -or [string]$healthParsed.action_id-cne$healthId -or [string]$healthParsed.claim_token-cne$claimId){throw "runner health result identity/status mismatch"}
    Assert-NoNewCodexProcess $codexBefore

    $task=Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $principal=Assert-InteractivePrincipal $task
    $priorXml=Export-ScheduledTask -TaskName $TaskName
    Write-Utf8NoBom $PriorTaskXmlPath $priorXml
    $priorState=[string]$task.State
    $priorActions=@($task.Actions|ForEach-Object{[ordered]@{execute=$_.Execute;arguments=$_.Arguments;working_directory=$_.WorkingDirectory}})

    $preEvidence=[ordered]@{
        protocol_version="scorp.exec/v4-bootstrap-evidence";commit_sha=$CommitSha.ToLowerInvariant();repo=$Repo;control_repo=$ControlRepo;task_name=$TaskName
        started_at=(Get-Date -Format o);phase="PRE_SWITCH_VERIFIED";principal=$principal;prior_state=$priorState;prior_actions=$priorActions
        manifest_git_blob_sha=$manifestObj.git_blob_sha;artifacts=$artifacts;self_test=$selfText;critical_test=$criticalText;production_test=$productionText;selfheal_test=$selfhealText
        runner_health=$healthParsed;rollback_performed=$false;rollback_error=$null
    }
    Write-Utf8NoBom $EvidencePath ($preEvidence|ConvertTo-Json -Depth 20)

    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskMutated=$true
    Wait-V4Quiescent -Root $InstallDir -Seconds 15

    if(Test-Path -LiteralPath $InstallDir -PathType Container){
        $priorInstallExisted=$true
        Move-Item -LiteralPath $InstallDir -Destination $BackupDir
    }
    Move-Item -LiteralPath $StagingDir -Destination $InstallDir
    $installMoved=$true

    $installedExecutor=Join-Path $InstallDir "executor-v4.1.ps1"
    $action=New-ScheduledTaskAction -Execute $WindowsPowerShell -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Repo "{1}"' -f $installedExecutor,$ControlRepo) -WorkingDirectory $InstallDir
    $runLevel=[string]$task.Principal.RunLevel
    if($runLevel-notin@("Highest","Limited")){throw "unsupported Scheduled Task RunLevel: $runLevel"}
    $normalizedPrincipal=New-ScheduledTaskPrincipal -UserId $principal.user_name -LogonType Interactive -RunLevel $runLevel
    $logonTrigger=New-ScheduledTaskTrigger -AtLogOn -User $principal.user_name
    $startupTrigger=New-ScheduledTaskTrigger -AtStartup
    $repeatTrigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
    $runtimeSettings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logonTrigger,$startupTrigger,$repeatTrigger) -Settings $runtimeSettings -Principal $normalizedPrincipal|Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3

    $after=Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $afterPrincipal=Assert-InteractivePrincipal $after
    if([string]$after.State-cne"Running"){throw "Scheduled Task is not Running after V4 switch: $($after.State)"}
    $afterActions=@($after.Actions)
    if($afterActions.Count-ne1 -or [string]$afterActions[0].Execute-cne$WindowsPowerShell -or [string]$afterActions[0].Arguments-notlike"*$installedExecutor*"){throw "Scheduled Task action verification failed"}
    $afterTriggerTypes=@($after.Triggers|ForEach-Object{$_.CimClass.CimClassName})
    foreach($requiredTrigger in @("MSFT_TaskLogonTrigger","MSFT_TaskBootTrigger","MSFT_TaskTimeTrigger")){if($afterTriggerTypes-notcontains$requiredTrigger){throw "Scheduled Task self-heal trigger missing: $requiredTrigger"}}
    if([string]$after.Settings.MultipleInstances-cne"IgnoreNew"){throw "Scheduled Task MultipleInstances is not IgnoreNew: $($after.Settings.MultipleInstances)"}
    if([int]$after.Settings.RestartCount-lt3){throw "Scheduled Task RestartCount is below 3: $($after.Settings.RestartCount)"}

    $probe=& $WindowsPowerShell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installedExecutor -MutexProbe 2>&1
    $probeText=$probe-join"`n"
    if($LASTEXITCODE-ne0 -or $probeText-notmatch'MUTEX_DENIED' -or $probeText-match'MUTEX_ACQUIRED'){throw "production mutex verification failed: $probeText"}
    Assert-NoNewCodexProcess $codexBefore

    $final=Get-Content -LiteralPath $EvidencePath -Raw|ConvertFrom-Json
    $final.phase="SWITCH_VERIFIED"
    $final|Add-Member -NotePropertyName deployed_at -NotePropertyValue (Get-Date -Format o) -Force
    $final|Add-Member -NotePropertyName deployed_action -NotePropertyValue ([ordered]@{execute=$afterActions[0].Execute;arguments=$afterActions[0].Arguments;working_directory=$afterActions[0].WorkingDirectory}) -Force
    $final|Add-Member -NotePropertyName deployed_principal -NotePropertyValue $afterPrincipal -Force
    $final|Add-Member -NotePropertyName deployed_trigger_types -NotePropertyValue $afterTriggerTypes -Force
    $final|Add-Member -NotePropertyName deployed_settings -NotePropertyValue ([ordered]@{multiple_instances=[string]$after.Settings.MultipleInstances;restart_count=[int]$after.Settings.RestartCount;restart_interval=[string]$after.Settings.RestartInterval;start_when_available=[bool]$after.Settings.StartWhenAvailable}) -Force
    $final|Add-Member -NotePropertyName mutex_probe -NotePropertyValue $probeText -Force
    $rollbackDirValue=$null
    if($priorInstallExisted){$rollbackDirValue=$BackupDir}
    $final|Add-Member -NotePropertyName rollback_dir -NotePropertyValue $rollbackDirValue -Force
    Write-Utf8NoBom $EvidencePath ($final|ConvertTo-Json -Depth 20)

    Write-Output "GPT_NATIVE_V4_BOOTSTRAP_PASS"
    Write-Output "Commit: $CommitSha"
    Write-Output "Executor: $installedExecutor"
    Write-Output "Evidence: $EvidencePath"
}
catch {
    $failure=$_.Exception.Message
    $rollbackError=$null
    if($taskMutated){
        try{
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if($priorXml){Register-ScheduledTask -TaskName $TaskName -Xml $priorXml -Force|Out-Null}
            if($installMoved -and (Test-Path -LiteralPath $InstallDir -PathType Container)){Remove-Item -LiteralPath $InstallDir -Recurse -Force}
            if($priorInstallExisted -and (Test-Path -LiteralPath $BackupDir -PathType Container)){Move-Item -LiteralPath $BackupDir -Destination $InstallDir}
            if($priorState-ceq"Running"){Start-ScheduledTask -TaskName $TaskName}
        }catch{$rollbackError=$_.Exception.Message}
    }
    try{
        if(Test-Path -LiteralPath $StagingDir -PathType Container){Remove-Item -LiteralPath $StagingDir -Recurse -Force}
        $evidence=if(Test-Path -LiteralPath $EvidencePath -PathType Leaf){Get-Content -LiteralPath $EvidencePath -Raw|ConvertFrom-Json}else{[pscustomobject][ordered]@{protocol_version="scorp.exec/v4-bootstrap-evidence";commit_sha=$CommitSha.ToLowerInvariant();repo=$Repo;control_repo=$ControlRepo;task_name=$TaskName;started_at=(Get-Date -Format o)}}
        $evidence|Add-Member -NotePropertyName phase -NotePropertyValue "FAILED_ROLLED_BACK" -Force
        $evidence|Add-Member -NotePropertyName failed_at -NotePropertyValue (Get-Date -Format o) -Force
        $evidence|Add-Member -NotePropertyName failure -NotePropertyValue $failure -Force
        $evidence|Add-Member -NotePropertyName rollback_performed -NotePropertyValue ([bool]$taskMutated) -Force
        $evidence|Add-Member -NotePropertyName rollback_error -NotePropertyValue $rollbackError -Force
        Write-Utf8NoBom $EvidencePath ($evidence|ConvertTo-Json -Depth 20)
    }catch{}
    if($rollbackError){throw "V4 bootstrap failed: $failure; ROLLBACK ALSO FAILED: $rollbackError"}
    throw "V4 bootstrap failed and rollback completed: $failure"
}
finally {
    if(Test-Path -LiteralPath $StagingDir -PathType Container){Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue}
}