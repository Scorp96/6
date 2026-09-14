param(
    [switch]$LibraryMode,
    [switch]$MutexProbe,
    [string]$MutexNameOverride,
    [string]$Root='C:\ScorpAgent\orchestrator-v1',
    [string]$RepoFullName='Scorp96/666',
    [string]$TrustedActor='Scorp96',
    [switch]$Once,
    [int]$PollSeconds=5
)

Set-StrictMode -Version 2.0
$ErrorActionPreference='Stop'

function Get-OrchestratorMutexName {
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $machine=[Environment]::MachineName
    return "Global\ScorpFullAutoOrchestratorV1-$machine-$sid"
}

function Try-AcquireOrchestratorMutex {
    param([string]$Name)
    if([string]::IsNullOrWhiteSpace($Name)){$Name=Get-OrchestratorMutexName}
    $mutex=New-Object Threading.Mutex($false,$Name)
    $acquired=$false
    try{
        try{$acquired=$mutex.WaitOne(0)}catch [System.Threading.AbandonedMutexException] {$acquired=$true}
        return [pscustomobject]@{Acquired=[bool]$acquired;Mutex=$mutex;Name=$Name}
    }catch{
        $mutex.Dispose()
        throw
    }
}

function Test-OrchestratorGitHubContext {
    param($Context)
    return ((Test-HasProperty $Context 'GitHub') -and $null-ne$Context.GitHub -and (Test-HasProperty $Context 'TrustedActor') -and -not[string]::IsNullOrWhiteSpace([string]$Context.TrustedActor))
}

function Get-PreAuthoredSuccessAction {
    param($Task)
    if($null-eq$Task.checkpoint){return $null}
    if($Task.checkpoint-is[string]){return $null}
    if(-not(Test-HasProperty $Task.checkpoint 'next_action_on_success')){return $null}
    $action=$Task.checkpoint.next_action_on_success
    if($null-eq$action){return $null}
    if(-not(Test-HasProperty $action 'protocol_version') -or [string]$action.protocol_version-cne'scorp.exec/v4'){throw 'CHECKPOINT_NEXT_ACTION_PROTOCOL_INVALID'}
    if(-not(Test-HasProperty $action 'action_id') -or [string]::IsNullOrWhiteSpace([string]$action.action_id)){throw 'CHECKPOINT_NEXT_ACTION_ID_INVALID'}
    return $action
}

function Ensure-ContinuationRequestForTask {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Task)
    if([string]$Task.state-cne'WAITING' -or [string]$Task.waiting_reason-cne'GPT_CONTINUATION_REQUIRED'){
        return [pscustomobject]@{status='NOT_REQUIRED';task=$Task;request=$null}
    }
    if(-not(Test-OrchestratorGitHubContext $Context)){
        return [pscustomobject]@{status='WAITING_LOCAL_ONLY';task=$Task;request=$null}
    }
    if((Test-HasProperty $Task 'continuation_request_id') -and -not[string]::IsNullOrWhiteSpace([string]$Task.continuation_request_id)){
        return [pscustomobject]@{status='ALREADY_REQUESTED';task=$Task;request=$null}
    }
    $registry=Read-RegistrySnapshot -Root ([string]$Context.Root)
    $requestSequence=[int64]$registry.sequence+1
    $result=$Task.last_result
    if($null-eq$result){
        $result=[pscustomobject]@{action_id=$null;result_sha256=$null;status=$null}
    }
    $request=New-ContinuationRequest -Task $Task -Result $result -Question 'Choose the next exact deterministic action or terminal state for this task.' -RegistrySequence $requestSequence
    $bound=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$Task.task_id) -Mutation ([pscustomobject]@{kind='SET_CONTINUATION_REQUEST';expected_generation=[int64]$Task.generation;request_id=[string]$request.request_id;updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
    try{
        $published=Publish-ContinuationRequest -Context $Context -Request $request
        return [pscustomobject]@{status='REQUESTED';task=$bound;request=$published}
    }catch{
        $reason='CONTINUATION_PUBLICATION_AMBIGUOUS: '+$_.Exception.Message
        $blocked=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$Task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$bound.generation;reason=$reason;updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
        return [pscustomobject]@{status='BLOCKED';task=$blocked;request=$request;reason=$reason}
    }
}

function Invoke-OnePendingContinuationDecision {
    param([Parameter(Mandatory=$true)]$Context)
    if(-not(Test-OrchestratorGitHubContext $Context)){return [pscustomobject]@{status='NONE';task_id=$null}}
    $registry=Read-RegistrySnapshot -Root ([string]$Context.Root)
    $pending=@($registry.tasks|Where-Object{[string]$_.state-ceq'WAITING' -and [string]$_.waiting_reason-ceq'GPT_CONTINUATION_REQUIRED' -and (Test-HasProperty $_ 'continuation_request_id') -and -not[string]::IsNullOrWhiteSpace([string]$_.continuation_request_id)}|Sort-Object @{Expression={[int]$_.priority};Descending=$true},@{Expression={[string]$_.task_id};Ascending=$true})
    if($pending.Count-eq0){return [pscustomobject]@{status='NONE';task_id=$null}}
    $task=$pending[0]
    $outbox=Get-ContinuationOutboxPath -Root ([string]$Context.Root) -RequestId ([string]$task.continuation_request_id)
    if(-not(Test-Path -LiteralPath $outbox -PathType Leaf)){
        $blocked=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$task.generation;reason='CONTINUATION_OUTBOX_MISSING';updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
        return [pscustomobject]@{status='BLOCKED';task_id=[string]$blocked.task_id}
    }
    try{$request=Get-Content -LiteralPath $outbox -Raw|ConvertFrom-Json}catch{
        $blocked=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$task.generation;reason='CONTINUATION_OUTBOX_INVALID';updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
        return [pscustomobject]@{status='BLOCKED';task_id=[string]$blocked.task_id}
    }
    try{
        $read=Read-ContinuationDecision -Context $Context -Request $request
        if([string]$read.status-cne'DECISION'){return [pscustomobject]@{status='WAITING';task_id=[string]$task.task_id}}
        $applied=Apply-ContinuationDecision -Root ([string]$Context.Root) -Task $task -Decision $read.decision
        $cleanup=Finalize-ContinuationRequest -Context $Context -Request $request -Decision $read.decision
        return [pscustomobject]@{status='APPLIED';task_id=[string]$task.task_id;decision_id=[string]$read.decision.decision_id;cleanup_status=[string]$cleanup.status;apply_status=[string]$applied.status}
    }catch{
        $reason='CONTINUATION_DECISION_BLOCKED: '+$_.Exception.Message
        $latest=Read-RegistrySnapshot -Root ([string]$Context.Root)
        $live=@($latest.tasks|Where-Object{[string]$_.task_id-ceq[string]$task.task_id})
        if($live.Count-eq1 -and [string]$live[0].state-ceq'WAITING'){
            [void](Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$live[0].generation;reason=$reason;updated_at=([DateTimeOffset]$Context.Now).ToString('o')}))
        }
        return [pscustomobject]@{status='BLOCKED';task_id=[string]$task.task_id;reason=$reason}
    }
}

function Complete-OrchestratorV4Result {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Task,[Parameter(Mandatory=$true)]$Terminal)
    $result=[pscustomobject]@{
        protocol_version='scorp.orchestrator/v4-result-ref-v1'
        task_id=[string]$Task.task_id
        v4_task_id=[string]$Task.lease.v4_task_id
        action_id=[string]$Task.lease.v4_action_id
        issue_number=[int]$Task.lease.issue_number
        claim_token=[string]$Task.lease.claim_token
        status=[string]$Terminal.terminal_status
        exit_code=$Terminal.exit_code
        result_sha256=[string]$Terminal.result_sha256
        observed_at=([DateTimeOffset]$Context.Now).ToString('o')
    }
    $next=$null
    $terminalOnSuccess=$false
    if([string]$result.status-ceq'SUCCEEDED'){
        $next=Get-PreAuthoredSuccessAction -Task $Task
        if($null-ne$Task.checkpoint -and -not($Task.checkpoint-is[string]) -and (Test-HasProperty $Task.checkpoint 'terminal_on_success')){
            $terminalOnSuccess=[bool]$Task.checkpoint.terminal_on_success
        }
    }
    $mutation=[pscustomobject]@{kind='APPLY_V4_RESULT';expected_generation=[int64]$Task.generation;result=$result;next_action=$next;terminal_on_success=$terminalOnSuccess;updated_at=([DateTimeOffset]$Context.Now).ToString('o')}
    $mapped=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$Task.task_id) -Mutation $mutation
    if([string]$mapped.state-ceq'WAITING' -and [string]$mapped.waiting_reason-ceq'GPT_CONTINUATION_REQUIRED'){
        $cont=Ensure-ContinuationRequestForTask -Context $Context -Task $mapped
        return [pscustomobject]@{status='FINALIZED';task=$cont.task;mapped_state=[string]$cont.task.state;continuation_status=[string]$cont.status}
    }
    return [pscustomobject]@{status='FINALIZED';task=$mapped;mapped_state=[string]$mapped.state;continuation_status='NOT_REQUIRED'}
}

function Recover-OrchestratorState {
    param([Parameter(Mandatory=$true)]$Context)
    Assert-RequiredProperties $Context @('Root','Now') 'ORCH_CONTEXT'
    $registry=Recover-RegistryFromWal -Root ([string]$Context.Root)
    $running=@($registry.tasks|Where-Object{[string]$_.state-ceq'RUNNING'}|Sort-Object @{Expression={[string]$_.task_id};Ascending=$true})
    if($running.Count-eq0){return [pscustomobject]@{status='NO_RUNNING';task_id=$null}}
    if(-not(Test-OrchestratorGitHubContext $Context)){return [pscustomobject]@{status='MONITORING_LOCAL_ONLY';task_id=[string]$running[0].task_id}}
    $task=$running[0]
    if($null-eq$task.lease){
        $blocked=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$task.generation;reason='RUNNING_LEASE_MISSING';updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
        return [pscustomobject]@{status='BLOCKED';task_id=[string]$blocked.task_id;reason='RUNNING_LEASE_MISSING'}
    }
    if($null-eq$task.lease.issue_number -or [int]$task.lease.issue_number-le0){
        try{$matches=@(Find-V4IssueByIdentity -Context $Context -Lease $task.lease)}catch{
            $matches=@()
        }
        if($matches.Count-ne1){
            $reason=if($matches.Count-gt1){'DUPLICATE_V4_ISSUE'}else{'AMBIGUOUS_PUBLICATION'}
            $blocked=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$task.generation;reason=$reason;updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
            return [pscustomobject]@{status='BLOCKED';task_id=[string]$blocked.task_id;reason=$reason}
        }
        $task.lease.issue_number=Get-IssueNumber $matches[0]
        $task.lease.publication_state='RECONCILED'
        $task=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='UPDATE_LEASE';expected_generation=[int64]$task.generation;lease=$task.lease;updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
    }
    $priorClaim=[string]$task.lease.claim_token
    try{$reconciled=Reconcile-V4Lease -Context $Context -Task $task}catch{
        $reason='V4_RECONCILIATION_AMBIGUOUS: '+$_.Exception.Message
        $blocked=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$task.generation;reason=$reason;updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
        return [pscustomobject]@{status='BLOCKED';task_id=[string]$blocked.task_id;reason=$reason}
    }
    if([string]$reconciled.status-ceq'PENDING' -or [string]$reconciled.status-ceq'RUNNING'){
        if([string]$task.lease.claim_token-cne$priorClaim){
            [void](Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='UPDATE_LEASE';expected_generation=[int64]$task.generation;lease=$task.lease;updated_at=([DateTimeOffset]$Context.Now).ToString('o')}))
        }
        return [pscustomobject]@{status='MONITORING';task_id=[string]$task.task_id;v4_status=[string]$reconciled.status}
    }
    if([string]$reconciled.status-ceq'ALREADY_APPLIED'){
        return [pscustomobject]@{status='MONITORING';task_id=[string]$task.task_id;v4_status='ALREADY_APPLIED'}
    }
    if([string]$reconciled.status-cne'TERMINAL'){
        $reason='UNKNOWN_RECONCILIATION_STATUS '+[string]$reconciled.status
        $blocked=Invoke-RegistryMutation -Root ([string]$Context.Root) -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$task.generation;reason=$reason;updated_at=([DateTimeOffset]$Context.Now).ToString('o')})
        return [pscustomobject]@{status='BLOCKED';task_id=[string]$blocked.task_id;reason=$reason}
    }
    return Complete-OrchestratorV4Result -Context $Context -Task $task -Terminal $reconciled
}

function Invoke-OrchestratorTick {
    param([Parameter(Mandatory=$true)]$Context)
    Assert-RequiredProperties $Context @('Root','Now') 'ORCH_CONTEXT'
    $root=[string]$Context.Root
    $now=[DateTimeOffset]$Context.Now

    # Active work is reconciled before pause so pause cannot strand a terminal result.
    $registry=Recover-RegistryFromWal -Root $root
    if(@($registry.tasks|Where-Object{[string]$_.state-ceq'RUNNING'}).Count-gt0 -and (Test-OrchestratorGitHubContext $Context)){
        $recovery=Recover-OrchestratorState -Context $Context
        if([string]$recovery.status-cne'NO_RUNNING'){
            return [pscustomobject]@{status=[string]$recovery.status;task_id=if(Test-HasProperty $recovery 'task_id'){$recovery.task_id}else{$null}}
        }
    }

    # A pending GPT decision is an already-authored mutation, not local reasoning.
    if(Test-OrchestratorGitHubContext $Context){
        $decision=Invoke-OnePendingContinuationDecision -Context $Context
        if([string]$decision.status-ceq'APPLIED' -or [string]$decision.status-ceq'BLOCKED'){
            return [pscustomobject]@{status=[string]$decision.status;task_id=$decision.task_id}
        }
    }

    $control=Get-ControlState -Root $root
    if([bool]$control.emergency_stop){return [pscustomobject]@{status='EMERGENCY_STOP';task_id=$null}}
    if([bool]$control.paused){return [pscustomobject]@{status='PAUSED';task_id=$null}}

    $registry=Recover-RegistryFromWal -Root $root
    $missing=@($registry.tasks|Where-Object{[string]$_.state-ceq'READY' -and $null-eq$_.next_action}|Sort-Object @{Expression={[int]$_.priority};Descending=$true},@{Expression={[string]$_.task_id};Ascending=$true})
    if($missing.Count-gt0){
        $task=$missing[0]
        $waiting=Invoke-RegistryMutation -Root $root -TaskId ([string]$task.task_id) -Mutation ([pscustomobject]@{kind='SET_WAITING';expected_generation=[int64]$task.generation;waiting_reason='GPT_CONTINUATION_REQUIRED';updated_at=$now.ToString('o')})
        if(Test-OrchestratorGitHubContext $Context){
            $c=Ensure-ContinuationRequestForTask -Context $Context -Task $waiting
            return [pscustomobject]@{status=if([string]$c.status-ceq'BLOCKED'){'BLOCKED'}else{'WAITING_GPT'};task_id=[string]$c.task.task_id}
        }
        return [pscustomobject]@{status='WAITING_GPT';task_id=[string]$task.task_id}
    }

    $selected=Select-NextTask -Registry $registry -Now $now
    if($null-eq$selected){return [pscustomobject]@{status='IDLE';task_id=$null}}
    if(-not(Test-OrchestratorGitHubContext $Context)){
        return [pscustomobject]@{status='READY_SELECTED';task_id=[string]$selected.task_id;action_id=[string]$selected.next_action.action_id}
    }

    $lease=New-V4Lease -Task $selected -Action $selected.next_action
    $running=Invoke-RegistryMutation -Root $root -TaskId ([string]$selected.task_id) -Mutation ([pscustomobject]@{kind='SET_RUNNING_LEASE';expected_generation=[int64]$selected.generation;lease=$lease;updated_at=$now.ToString('o')})
    try{
        $published=Publish-V4Action -Context $Context -Task $running -Lease $running.lease -Action $running.next_action
        $running=Invoke-RegistryMutation -Root $root -TaskId ([string]$running.task_id) -Mutation ([pscustomobject]@{kind='UPDATE_LEASE';expected_generation=[int64]$running.generation;lease=$published;updated_at=$now.ToString('o')})
        return [pscustomobject]@{status='RUNNING';task_id=[string]$running.task_id;action_id=[string]$running.next_action.action_id;issue_number=[int]$running.lease.issue_number}
    }catch{
        $reason='V4_PUBLICATION_BLOCKED: '+$_.Exception.Message
        $blocked=Invoke-RegistryMutation -Root $root -TaskId ([string]$running.task_id) -Mutation ([pscustomobject]@{kind='SET_BLOCKED';expected_generation=[int64]$running.generation;reason=$reason;updated_at=$now.ToString('o')})
        return [pscustomobject]@{status='BLOCKED';task_id=[string]$blocked.task_id;reason=$reason}
    }
}

function Get-CheckpointBoundaryViolations {
    param([Parameter(Mandatory=$true)]$Registry,[Parameter(Mandatory=$true)][DateTimeOffset]$Now)
    Assert-RegistrySnapshot $Registry
    $violations=@()
    foreach($task in @($Registry.tasks)){
        if(@('DONE','FAILED','BLOCKED') -contains [string]$task.state){continue}
        if($null-eq$task.checkpoint -or $task.checkpoint-is[string] -or -not(Test-HasProperty $task.checkpoint 'deadline_at')){continue}
        $raw=[string]$task.checkpoint.deadline_at
        if([string]::IsNullOrWhiteSpace($raw)){continue}
        try{$deadline=[DateTimeOffset]::Parse($raw)}catch{
            $violations+=,[pscustomobject]@{task_id=[string]$task.task_id;reason='CHECKPOINT_DEADLINE_INVALID';deadline_at=$raw;observed_at=$Now.ToString('o')}
            continue
        }
        if($Now-gt$deadline){$violations+=,[pscustomobject]@{task_id=[string]$task.task_id;reason='CHECKPOINT_DEADLINE_EXPIRED';deadline_at=$raw;observed_at=$Now.ToString('o')}}
    }
    return $violations
}

function Invoke-OrchestratorTickSafe {
    param([Parameter(Mandatory=$true)]$Context)
    try{
        $tick=Invoke-OrchestratorTick -Context $Context
        $registry=Recover-RegistryFromWal -Root ([string]$Context.Root)
        $violations=@(Get-CheckpointBoundaryViolations -Registry $registry -Now ([DateTimeOffset]$Context.Now))
        $healthStatus=if($violations.Count-gt0){'CHECKPOINT_BOUNDARY_VIOLATION'}else{[string]$tick.status}
        $health=[ordered]@{
            protocol_version='scorp.orchestrator/health-v1'
            status=$healthStatus
            heartbeat_at=([DateTimeOffset]$Context.Now).ToString('o')
            queue_depth=@($registry.tasks|Where-Object{[string]$_.state-ceq'READY'}).Count
            current_task=if(Test-HasProperty $tick 'task_id'){$tick.task_id}else{$null}
            orchestrator_pid=$PID
            tick_error=$null
            checkpoint_boundary_violations=$violations
        }
        [void](Set-HealthState -Root ([string]$Context.Root) -Health $health)
        return $tick
    }catch{
        $message=[string]$_.Exception.Message
        if($message.Length-gt2048){$message=$message.Substring(0,2048)+' [truncated]'}
        $degraded=[pscustomobject]@{status='DEGRADED';task_id=$null;tick_error=$message}
        $health=[ordered]@{
            protocol_version='scorp.orchestrator/health-v1'
            status='DEGRADED'
            heartbeat_at=([DateTimeOffset]$Context.Now).ToString('o')
            queue_depth=$null
            current_task=$null
            orchestrator_pid=$PID
            tick_error=$message
        }
        try{[void](Set-HealthState -Root ([string]$Context.Root) -Health $health)}catch{}
        return $degraded
    }
}

if($LibraryMode){return}

if($MutexProbe){
    $probe=Try-AcquireOrchestratorMutex -Name $MutexNameOverride
    try{
        if($probe.Acquired){Write-Output 'MUTEX_ACQUIRED'}else{Write-Output 'MUTEX_DENIED'}
    }finally{
        if($probe.Acquired){try{$probe.Mutex.ReleaseMutex()}catch{}}
        $probe.Mutex.Dispose()
    }
    exit 0
}

$moduleRoot=Join-Path $PSScriptRoot 'orchestrator'
. (Join-Path $moduleRoot 'store-v1.ps1')
. (Join-Path $moduleRoot 'registry-v1.ps1')
. (Join-Path $moduleRoot 'health-control-v1.ps1')
. (Join-Path $moduleRoot 'router-v4.ps1')
. (Join-Path $moduleRoot 'continuation-v1.ps1')

$github=New-GitHubCliAdapter -RepoFullName $RepoFullName

$guard=Try-AcquireOrchestratorMutex
if(-not$guard.Acquired){
    $guard.Mutex.Dispose()
    Write-Output 'MUTEX_DENIED'
    exit 0
}
try{
    do{
        $now=[DateTimeOffset]::UtcNow
        $context=[pscustomobject]@{Root=$Root;Now=$now;GitHub=$github;TrustedActor=$TrustedActor}
        $tick=Invoke-OrchestratorTickSafe -Context $context
        if($Once){break}
        Start-Sleep -Seconds ([Math]::Max(1,$PollSeconds))
    }while($true)
}finally{
    try{$guard.Mutex.ReleaseMutex()}catch{}
    $guard.Mutex.Dispose()
}
