<#
.SYNOPSIS
Read-only P0 Windows authority/ACL evidence. Does not install, restart or mutate.
.DESCRIPTION
Capture a bounded snapshot of service/task identities and selected trust-boundary
filesystem objects. A PASS result is intentionally NOT a security attestation:
effective token membership, parent DELETE_CHILD, reparse races and arbitrary
PowerShell authority require separate live review.
#>
param(
    [string]$AgentRoot='C:\ScorpAgent',
    [string]$StateRoot='C:\ProgramData\ScorpAgent\privileged-broker',
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$ServiceName='ScorpPrivilegedBroker',
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$TaskName='ScorpComputerAgent'
)
$ErrorActionPreference='Stop'
$trustedSid=@('S-1-5-18','S-1-5-32-544')
$dangerMask=([int64][Security.AccessControl.FileSystemRights]::Write -bor
    [int64][Security.AccessControl.FileSystemRights]::Modify -bor
    [int64][Security.AccessControl.FileSystemRights]::FullControl -bor
    [int64][Security.AccessControl.FileSystemRights]::Delete -bor
    [int64][Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [int64][Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [int64][Security.AccessControl.FileSystemRights]::TakeOwnership)
$readMask=([int64][Security.AccessControl.FileSystemRights]::Read -bor
    [int64][Security.AccessControl.FileSystemRights]::ReadData)
function Get-SidValue($Reference){
    try {
        if($Reference -is [Security.Principal.SecurityIdentifier]){return $Reference.Value}
        return $Reference.Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {return $null}
}
function Inspect-Object([string]$Name,[string]$Path){
    $result=[ordered]@{
        name=$Name;path=$Path;present=$false;owner_sid=$null
        owner_trusted=$false;reparse_point=$null
        unexpected_write_aces=@();unexpected_read_aces=@()
        acl_error=$null;sha256=$null
    }
    try{
        $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        $result.present=$true
        $result.reparse_point=([bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint))
        $acl=Get-Acl -LiteralPath $Path -ErrorAction Stop
        $result.owner_sid=Get-SidValue ($acl.GetOwner([Security.Principal.SecurityIdentifier]))
        $result.owner_trusted=($trustedSid -contains $result.owner_sid)
        foreach($ace in $acl.Access){
            if($ace.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow){continue}
            $sid=Get-SidValue $ace.IdentityReference
            $rights=[int64]$ace.FileSystemRights
            $record=[ordered]@{sid=$sid;rights=[string]$ace.FileSystemRights;inherited=[bool]$ace.IsInherited}
            if($null-eq$sid -or -not($trustedSid -contains $sid)){
                if(($rights -band $dangerMask) -ne 0){$result.unexpected_write_aces+= $record}
                if($Name -eq 'broker_signing_key' -and ($rights -band $readMask) -ne 0){
                    $result.unexpected_read_aces+= $record
                }
            }
        }
        if($item -is [IO.FileInfo] -and $Name -in @('broker_service_source','broker_client_source','broker_python','runner_source','executor_source')){
            $result.sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } catch {
        $result.acl_error=$_.Exception.GetType().Name+': '+$_.Exception.Message
    }
    return [pscustomobject]$result
}
$install=Join-Path $AgentRoot 'privileged-broker'
$runtime=Join-Path $AgentRoot 'privileged-broker-runtime'
$targets=[ordered]@{
    agent_root=$AgentRoot
    broker_install_root=$install
    broker_service_source=(Join-Path $install 'broker_service.py')
    broker_client_source=(Join-Path $install 'broker_client.py')
    broker_runtime_root=$runtime
    broker_python=(Join-Path $runtime 'Scripts\python.exe')
    broker_state_root=$StateRoot
    broker_signing_key=(Join-Path $StateRoot 'secret.key')
    runner_source=(Join-Path $AgentRoot 'runner-v4.1.ps1')
    executor_source=(Join-Path $AgentRoot 'executor-v4.1.ps1')
}
$objects=@()
foreach($name in $targets.Keys){$objects+=Inspect-Object $name ([string]$targets[$name])}
$service=[ordered]@{found=$false;identity=$null;start_mode=$null;state=$null;path=$null;error=$null}
try{
    $svc=Get-CimInstance Win32_Service -Filter ("Name='{0}'" -f $ServiceName) -ErrorAction Stop
    if($null-ne$svc){
        $service.found=$true;$service.identity=[string]$svc.StartName
        $service.start_mode=[string]$svc.StartMode
        $service.state=[string]$svc.State;$service.path=[string]$svc.PathName
    }
}catch{$service.error=$_.Exception.Message}
$task=[ordered]@{found=$false;user_id=$null;run_level=$null;logon_type=$null;error=$null}
try{
    $scheduled=Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if($null-ne$scheduled){
        $task.found=$true
        $task.user_id=[string]$scheduled.Principal.UserId
        $task.run_level=[string]$scheduled.Principal.RunLevel
        $task.logon_type=[string]$scheduled.Principal.LogonType
    }
}catch{$task.error=$_.Exception.Message}
$findings=@()
foreach($o in $objects){
    if(-not $o.present){$findings+="MISSING:$($o.name)";continue}
    if($o.acl_error){$findings+="ACL_UNVERIFIED:$($o.name)";continue}
    if(-not $o.owner_trusted){$findings+="UNTRUSTED_OWNER:$($o.name)"}
    if($o.reparse_point){$findings+="REPARSE_POINT:$($o.name)"}
    if(@($o.unexpected_write_aces).Count -gt 0){$findings+="UNEXPECTED_WRITE_ACL:$($o.name)"}
    if(@($o.unexpected_read_aces).Count -gt 0){$findings+="BROKER_KEY_READABLE_BY_UNTRUSTED_SID"}
}
if(-not $service.found){$findings+='BROKER_SERVICE_ABSENT_OR_UNVERIFIED'}
elseif($service.identity -ne 'LocalSystem'){$findings+='BROKER_SERVICE_NOT_LOCALSYSTEM'}
if(-not $task.found){$findings+='EXECUTOR_TASK_ABSENT_OR_UNVERIFIED'}
if($task.run_level -eq 'Highest'){$findings+='EXECUTOR_RUNS_HIGHEST_PRIVILEGES'}
[pscustomobject][ordered]@{
    protocol='scorp.p0-host-authority-observer/1'
    observed_at_utc=[DateTimeOffset]::UtcNow.ToString('o')
    mode='READ_ONLY'
    conclusion='REVIEW_REQUIRED_NOT_A_CERTIFICATION'
    limitations=@('No token effective-access simulation','No reparse race proof',
                  'No per-task human approval proof','No production write or restart')
    objects=$objects;service=$service;executor_task=$task;findings=@($findings)
} | ConvertTo-Json -Depth 12 -Compress
