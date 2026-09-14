param(
    [Parameter(Mandatory=$true)][string]$StorePath,
    [Parameter(Mandatory=$true)][string]$RegistryPath,
    [Parameter(Mandatory=$true)][string]$HealthPath,
    [Parameter(Mandatory=$true)][string]$RouterPath,
    [Parameter(Mandatory=$true)][string]$ContinuationPath,
    [Parameter(Mandatory=$true)][string]$EntrypointPath
)

$ErrorActionPreference='Stop'

function Assert-True {
    param([bool]$Condition,[string]$Name)
    if(-not$Condition){throw "ORCHESTRATOR_V1_STATE_MACHINE_RECOVERY_FAIL: $Name"}
    Write-Output "PASS $Name"
}

foreach($p in @($StorePath,$RegistryPath,$HealthPath,$RouterPath,$ContinuationPath,$EntrypointPath)){
    if(-not(Test-Path -LiteralPath $p -PathType Leaf)){throw "ORCH_TASK5_MODULE_MISSING: $p"}
}
. $StorePath
. $RegistryPath
. $HealthPath
. $RouterPath
. $ContinuationPath
. $EntrypointPath -LibraryMode

foreach($fn in @('Invoke-OrchestratorTick','Recover-OrchestratorState','Invoke-RegistryMutation','New-V4Lease','Publish-V4Action','Reconcile-V4Lease','New-ContinuationRequest','Publish-ContinuationRequest')){
    Assert-True ($null-ne(Get-Command $fn -CommandType Function -ErrorAction SilentlyContinue)) ("function-"+$fn)
}

function New-TestTask {
    param(
        [string]$Id,
        [string]$State='READY',
        $NextAction=$null,
        $Checkpoint=$null,
        [int]$Generation=0,
        $Lease=$null,
        $LastResult=$null,
        [string]$WaitingReason=$null
    )
    [pscustomobject]@{
        protocol_version='scorp.orchestrator/task-v1';task_id=$Id;project_id='p';priority=100;state=$State;dependencies=@();dependency_policy='all_done';parallel_safety=$false;execution_adapter='LOCAL_V4';next_action=$NextAction;continuation_request_id=$null;checkpoint=$Checkpoint;attempts=0;max_attempts=3;next_eligible_at=$null;created_at='2026-09-10T00:00:00Z';updated_at='2026-09-10T00:00:00Z';last_result=$LastResult;evidence_refs=@();safety_class='standard';authorization_requirement=$null;waiting_reason=$WaitingReason;blocker=$null;lease=$Lease;generation=$Generation;resource_scope=@()
    }
}

function Write-TestRegistry {
    param([string]$Root,$Tasks,[int64]$Sequence=0)
    if(Test-Path -LiteralPath $Root){Remove-Item -LiteralPath $Root -Recurse -Force}
    New-Item -ItemType Directory -Path $Root|Out-Null
    Write-RegistrySnapshot -Root $Root -Registry ([ordered]@{protocol_version='scorp.orchestrator/v1';sequence=$Sequence;tasks=@($Tasks);updated_at='2026-09-10T00:00:00Z'})
}

function New-FakeGitHubState {
    [pscustomobject]@{issues=@();comments=@{};create_calls=0;update_calls=0;next_issue=901;create_assertion=$null}
}

function New-FakeGitHub {
    param($State)
    $create={
        param($title,$body)
        $State.create_calls++
        if($null-ne$State.create_assertion){& $State.create_assertion $title $body}
        $n=[int]$State.next_issue;$State.next_issue++
        $issue=[pscustomobject]@{number=$n;title=$title;body=$body;state='open'}
        $State.issues+=,$issue
        return $issue
    }.GetNewClosure()
    $search={param($identity)@($State.issues)}.GetNewClosure()
    $readIssue={param($number)@($State.issues|Where-Object{[int]$_.number-eq[int]$number})[0]}.GetNewClosure()
    $readComments={param($number)if($State.comments.ContainsKey([int]$number)){@($State.comments[[int]$number])}else{@()}}.GetNewClosure()
    $update={param($number,$title,$state)$State.update_calls++;$i=@($State.issues|Where-Object{[int]$_.number-eq[int]$number})[0];if($null-ne$i){$i.title=$title;$i.state=$state};return $i}.GetNewClosure()
    [pscustomobject]@{CreateIssue=$create;SearchIssues=$search;ReadIssue=$readIssue;ReadComments=$readComments;UpdateIssue=$update}
}

function Set-V4Lifecycle {
    param($State,$Task,[string]$Status,[string]$ResultHash=('a'*64),[int]$ExitCode=0,[switch]$RunningOnly)
    $lease=$Task.lease
    $claim='claim-'+([string]$lease.v4_action_id)
    $comments=@([pscustomobject]@{author='Scorp96';body="SCORP_EXEC_CLAIMED`nProtocol: scorp.exec/v4`nIssue: $($lease.issue_number)`nTaskId: $($lease.v4_task_id)`nActionId: $($lease.v4_action_id)`nClaimToken: $claim"})
    if(-not$RunningOnly){
        $comments+=,[pscustomobject]@{author='Scorp96';body="SCORP_EXEC_RESULT`nProtocol: scorp.exec/v4`nIssue: $($lease.issue_number)`nTaskId: $($lease.v4_task_id)`nActionId: $($lease.v4_action_id)`nClaimToken: $claim`nStatus: $Status`nExitCode: $ExitCode`nResultSHA256: $ResultHash"}
    }
    $State.comments[[int]$lease.issue_number]=$comments
}

$base=Join-Path $env:TEMP ('scorp-orch-task5-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $base|Out-Null
try{
    # READY -> RUNNING lease must be durable before CreateIssue.
    $root1=Join-Path $base 'ready-running'
    $a1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='a-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    $a2=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='a-002';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    $t1=New-TestTask -Id 'chain' -NextAction $a1 -Checkpoint ([pscustomobject]@{next_action_on_success=$a2})
    Write-TestRegistry -Root $root1 -Tasks @($t1)
    $s1=New-FakeGitHubState
    $s1.create_assertion={param($title,$body)$snap=Read-RegistrySnapshot -Root $root1;$live=@($snap.tasks|Where-Object task_id -EQ 'chain')[0];if([string]$live.state-cne'RUNNING'-or$null-eq$live.lease){throw 'LEASE_NOT_DURABLE_BEFORE_CREATE'}}
    $g1=New-FakeGitHub -State $s1
    $ctx1=[pscustomobject]@{Root=$root1;Now=[DateTimeOffset]::Parse('2026-09-10T03:00:00Z');GitHub=$g1;TrustedActor='Scorp96'}
    $tick1=Invoke-OrchestratorTick -Context $ctx1
    $r1=Read-RegistrySnapshot -Root $root1;$run1=@($r1.tasks|Where-Object task_id -EQ 'chain')[0]
    Assert-True ([string]$run1.state -ceq 'RUNNING' -and $null-ne$run1.lease) 'ready-persists-running-lease'
    Assert-True ([int]$run1.lease.issue_number -eq 901 -and $s1.create_calls-eq1) 'ready-publishes-one-v4-issue'

    # RUNNING + SUCCEEDED + pre-authored checkpoint action -> READY next generation, no GPT request.
    Set-V4Lifecycle -State $s1 -Task $run1 -Status 'SUCCEEDED'
    $ctx1.Now=[DateTimeOffset]::Parse('2026-09-10T03:01:00Z')
    $tick2=Invoke-OrchestratorTick -Context $ctx1
    $r2=Read-RegistrySnapshot -Root $root1;$next1=@($r2.tasks|Where-Object task_id -EQ 'chain')[0]
    Assert-True ([string]$next1.state -ceq 'READY' -and [string]$next1.next_action.action_id -ceq 'a-002') 'success-preauthored-next-ready'
    Assert-True ([int64]$next1.generation -eq 1) 'success-next-generation'
    Assert-True ($s1.create_calls-eq1) 'success-preauthored-no-continuation-issue'

    # RUNNING + SUCCEEDED without pre-authored next action -> WAITING + exactly one continuation request.
    $root2=Join-Path $base 'success-waiting'
    $b1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='b-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    $t2=New-TestTask -Id 'needs-gpt' -NextAction $b1
    Write-TestRegistry -Root $root2 -Tasks @($t2)
    $s2=New-FakeGitHubState;$g2=New-FakeGitHub -State $s2
    $ctx2=[pscustomobject]@{Root=$root2;Now=[DateTimeOffset]::Parse('2026-09-10T04:00:00Z');GitHub=$g2;TrustedActor='Scorp96'}
    [void](Invoke-OrchestratorTick -Context $ctx2)
    $r2a=Read-RegistrySnapshot -Root $root2;$run2=@($r2a.tasks|Where-Object task_id -EQ 'needs-gpt')[0]
    Set-V4Lifecycle -State $s2 -Task $run2 -Status 'SUCCEEDED'
    $ctx2.Now=[DateTimeOffset]::Parse('2026-09-10T04:01:00Z')
    [void](Invoke-OrchestratorTick -Context $ctx2)
    $r2b=Read-RegistrySnapshot -Root $root2;$wait2=@($r2b.tasks|Where-Object task_id -EQ 'needs-gpt')[0]
    Assert-True ([string]$wait2.state -ceq 'WAITING' -and [string]$wait2.waiting_reason -ceq 'GPT_CONTINUATION_REQUIRED') 'success-no-next-waits-gpt'
    Assert-True (-not[string]::IsNullOrWhiteSpace([string]$wait2.continuation_request_id)) 'success-no-next-binds-request'
    Assert-True ($s2.create_calls-eq2) 'success-no-next-one-v4-one-continuation'
    $beforeRepeat=$s2.create_calls
    [void](Invoke-OrchestratorTick -Context $ctx2)
    [void](Invoke-OrchestratorTick -Context $ctx2)
    Assert-True ($s2.create_calls-eq$beforeRepeat) 'unchanged-waiting-no-cloud-spin'

    # FAILED requests GPT once and does not blind retry V4.
    $root3=Join-Path $base 'failed'
    $c1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='c-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    Write-TestRegistry -Root $root3 -Tasks @((New-TestTask -Id 'failed-task' -NextAction $c1))
    $s3=New-FakeGitHubState;$g3=New-FakeGitHub -State $s3;$ctx3=[pscustomobject]@{Root=$root3;Now=[DateTimeOffset]::Parse('2026-09-10T05:00:00Z');GitHub=$g3;TrustedActor='Scorp96'}
    [void](Invoke-OrchestratorTick -Context $ctx3);$rr3=Read-RegistrySnapshot -Root $root3;$run3=@($rr3.tasks)[0];Set-V4Lifecycle -State $s3 -Task $run3 -Status 'FAILED' -ExitCode 1
    [void](Invoke-OrchestratorTick -Context $ctx3);$rr3b=Read-RegistrySnapshot -Root $root3;$failWait=@($rr3b.tasks)[0]
    Assert-True ([string]$failWait.state -ceq 'WAITING' -and [string]$failWait.waiting_reason -ceq 'GPT_CONTINUATION_REQUIRED') 'failed-waits-gpt'
    $callsAfterFail=$s3.create_calls;[void](Invoke-OrchestratorTick -Context $ctx3);Assert-True ($s3.create_calls-eq$callsAfterFail) 'failed-no-blind-retry-no-spin'

    # TIMED_OUT requests reasoning, never republishes the action.
    $root4=Join-Path $base 'timeout'
    $d1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='d-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    Write-TestRegistry -Root $root4 -Tasks @((New-TestTask -Id 'timeout-task' -NextAction $d1))
    $s4=New-FakeGitHubState;$g4=New-FakeGitHub -State $s4;$ctx4=[pscustomobject]@{Root=$root4;Now=[DateTimeOffset]::Parse('2026-09-10T06:00:00Z');GitHub=$g4;TrustedActor='Scorp96'}
    [void](Invoke-OrchestratorTick -Context $ctx4);$run4=@((Read-RegistrySnapshot -Root $root4).tasks)[0];Set-V4Lifecycle -State $s4 -Task $run4 -Status 'TIMED_OUT' -ExitCode 124
    [void](Invoke-OrchestratorTick -Context $ctx4);$to4=@((Read-RegistrySnapshot -Root $root4).tasks)[0]
    Assert-True ([string]$to4.state -ceq 'WAITING' -and [string]$to4.waiting_reason -ceq 'GPT_CONTINUATION_REQUIRED') 'timeout-no-blind-rerun'
    Assert-True ($s4.create_calls-eq2) 'timeout-one-v4-one-continuation'

    # BLOCKED propagates terminal blocker and creates no continuation issue.
    $root5=Join-Path $base 'blocked'
    $e1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='e-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    Write-TestRegistry -Root $root5 -Tasks @((New-TestTask -Id 'blocked-task' -NextAction $e1))
    $s5=New-FakeGitHubState;$g5=New-FakeGitHub -State $s5;$ctx5=[pscustomobject]@{Root=$root5;Now=[DateTimeOffset]::Parse('2026-09-10T07:00:00Z');GitHub=$g5;TrustedActor='Scorp96'}
    [void](Invoke-OrchestratorTick -Context $ctx5);$run5=@((Read-RegistrySnapshot -Root $root5).tasks)[0];Set-V4Lifecycle -State $s5 -Task $run5 -Status 'BLOCKED' -ExitCode 1
    [void](Invoke-OrchestratorTick -Context $ctx5);$bl5=@((Read-RegistrySnapshot -Root $root5).tasks)[0]
    Assert-True ([string]$bl5.state -ceq 'BLOCKED') 'blocked-propagates'
    Assert-True ($s5.create_calls-eq1) 'blocked-no-continuation-create'

    # Restart with linked RUNNING V4 adopts monitoring without second publication.
    $root6=Join-Path $base 'recover-running'
    $f1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='f-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    $seed6=New-TestTask -Id 'recover-running' -NextAction $f1
    $lease6=New-V4Lease -Task $seed6 -Action $f1;$lease6.issue_number=990;$lease6.publication_state='PUBLISHED';$seed6.state='RUNNING';$seed6.lease=$lease6
    Write-TestRegistry -Root $root6 -Tasks @($seed6)
    $s6=New-FakeGitHubState;$s6.issues+=,[pscustomobject]@{number=990;title='running';body=(Get-V4BindingText -Lease $lease6 -Action $f1);state='open'};$g6=New-FakeGitHub -State $s6;Set-V4Lifecycle -State $s6 -Task $seed6 -Status 'SUCCEEDED' -RunningOnly
    $ctx6=[pscustomobject]@{Root=$root6;Now=[DateTimeOffset]::Parse('2026-09-10T08:00:00Z');GitHub=$g6;TrustedActor='Scorp96'}
    $rec6=Recover-OrchestratorState -Context $ctx6
    Assert-True ([string]$rec6.status -ceq 'MONITORING') 'restart-running-adopts-monitor'
    Assert-True ($s6.create_calls-eq0) 'restart-running-no-second-issue'

    # Restart with terminal result finalizes once without new publication.
    $root7=Join-Path $base 'recover-terminal'
    $gAction=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='g-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    $seed7=New-TestTask -Id 'recover-terminal' -NextAction $gAction
    $lease7=New-V4Lease -Task $seed7 -Action $gAction;$lease7.issue_number=991;$lease7.publication_state='PUBLISHED';$seed7.state='RUNNING';$seed7.lease=$lease7
    Write-TestRegistry -Root $root7 -Tasks @($seed7)
    $s7=New-FakeGitHubState;$s7.issues+=,[pscustomobject]@{number=991;title='done';body=(Get-V4BindingText -Lease $lease7 -Action $gAction);state='closed'};$g7=New-FakeGitHub -State $s7;Set-V4Lifecycle -State $s7 -Task $seed7 -Status 'BLOCKED' -ExitCode 1
    $ctx7=[pscustomobject]@{Root=$root7;Now=[DateTimeOffset]::Parse('2026-09-10T09:00:00Z');GitHub=$g7;TrustedActor='Scorp96'}
    $rec7=Recover-OrchestratorState -Context $ctx7
    Assert-True ([string]$rec7.status -ceq 'FINALIZED') 'restart-terminal-finalizes'
    Assert-True ([string]@((Read-RegistrySnapshot -Root $root7).tasks)[0].state -ceq 'BLOCKED') 'restart-terminal-state-mapped'
    $seq7=[int64](Read-RegistrySnapshot -Root $root7).sequence;$rec7b=Recover-OrchestratorState -Context $ctx7;$seq7b=[int64](Read-RegistrySnapshot -Root $root7).sequence
    Assert-True ($seq7b-eq$seq7) 'restart-terminal-finalize-once'
    Assert-True ($s7.create_calls-eq0) 'restart-terminal-no-new-issue'

    # Ambiguous publication recovery never creates a new issue and blocks.
    $root8=Join-Path $base 'recover-ambiguous'
    $h1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='h-001';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
    $seed8=New-TestTask -Id 'recover-ambiguous' -NextAction $h1
    $lease8=New-V4Lease -Task $seed8 -Action $h1;$seed8.state='RUNNING';$seed8.lease=$lease8
    Write-TestRegistry -Root $root8 -Tasks @($seed8)
    $s8=New-FakeGitHubState;$g8=New-FakeGitHub -State $s8;$ctx8=[pscustomobject]@{Root=$root8;Now=[DateTimeOffset]::Parse('2026-09-10T10:00:00Z');GitHub=$g8;TrustedActor='Scorp96'}
    $rec8=Recover-OrchestratorState -Context $ctx8;$after8=@((Read-RegistrySnapshot -Root $root8).tasks)[0]
    Assert-True ([string]$rec8.status -ceq 'BLOCKED' -and [string]$after8.state -ceq 'BLOCKED') 'restart-ambiguous-publication-blocks'
    Assert-True ($s8.create_calls-eq0) 'restart-ambiguous-never-republishes'
}finally{
    Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output 'ORCHESTRATOR_V1_STATE_MACHINE_RECOVERY_PASS'
