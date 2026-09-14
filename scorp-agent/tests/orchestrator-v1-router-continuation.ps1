param(
    [Parameter(Mandatory=$true)][string]$StorePath,
    [Parameter(Mandatory=$true)][string]$RouterPath,
    [Parameter(Mandatory=$true)][string]$ContinuationPath
)

$ErrorActionPreference='Stop'

function Assert-True {
    param([bool]$Condition,[string]$Name)
    if(-not $Condition){throw "ORCHESTRATOR_V1_ROUTER_CONTINUATION_FAIL: $Name"}
    Write-Output "PASS $Name"
}

if(-not(Test-Path -LiteralPath $StorePath -PathType Leaf)){throw "ORCH_STORE_MISSING: $StorePath"}
if(-not(Test-Path -LiteralPath $RouterPath -PathType Leaf)){throw "ORCH_ROUTER_MODULE_MISSING: $RouterPath"}
. $StorePath
. $RouterPath

foreach($fn in @('New-V4Lease','Publish-V4Action','Find-V4IssueByIdentity','Read-V4Lifecycle','Reconcile-V4Lease')){
    Assert-True ($null-ne(Get-Command $fn -CommandType Function -ErrorAction SilentlyContinue)) ("function-"+$fn)
}

function New-Task {
    param([string]$Id='task-a',[int]$Generation=2,$Lease=$null)
    [pscustomobject]@{
        protocol_version='scorp.orchestrator/task-v1';task_id=$Id;project_id='p';priority=100;state='READY';dependencies=@();dependency_policy='all_done';parallel_safety=$false;execution_adapter='LOCAL_V4';next_action=$null;continuation_request_id=$null;checkpoint=$null;attempts=0;max_attempts=3;next_eligible_at=$null;created_at='2026-09-10T00:00:00Z';updated_at='2026-09-10T00:00:00Z';last_result=$null;evidence_refs=@();safety_class='standard';authorization_requirement=$null;waiting_reason=$null;blocker=$null;lease=$Lease;generation=$Generation;resource_scope=@()
    }
}

$action=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='act-a';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
$task=New-Task
$lease=New-V4Lease -Task $task -Action $action
Assert-True ([string]$lease.orchestrator_task_id -ceq 'task-a') 'lease-binds-orchestrator-task'
Assert-True ([int]$lease.orchestrator_generation -eq 2) 'lease-binds-generation'
Assert-True ([string]$lease.v4_task_id -ceq 'orch/task-a/g2') 'lease-deterministic-v4-task-id'
Assert-True ([string]$lease.v4_action_id -ceq 'act-a') 'lease-binds-action-id'
Assert-True ([string]$lease.envelope_hash -cmatch '^[0-9a-f]{64}$') 'lease-binds-envelope-hash'
Assert-True (-not[string]::IsNullOrWhiteSpace([string]$lease.lease_id)) 'lease-has-id'

$script:CreateCalls=0
$script:FakeIssues=@()
$github=[pscustomobject]@{
    CreateIssue={param($title,$body)$script:CreateCalls++;[pscustomobject]@{number=501;title=$title;body=$body}}
    SearchIssues={param($identity)@($script:FakeIssues)}
    ReadIssue={param($number)@($script:FakeIssues|Where-Object{[int]$_.number-eq[int]$number})[0]}
    ReadComments={param($number)@()}
}
$context=[pscustomobject]@{GitHub=$github;TrustedActor='Scorp96'}
$notPersistedFailed=$false
try{[void](Publish-V4Action -Context $context -Task $task -Lease $lease -Action $action)}catch{$notPersistedFailed=$_.Exception.Message -match 'LEASE_NOT_PERSISTED'}
Assert-True $notPersistedFailed 'lease-persisted-before-create'
Assert-True ($script:CreateCalls -eq 0) 'create-not-called-before-lease-persisted'
$task.lease=$lease
$published=Publish-V4Action -Context $context -Task $task -Lease $lease -Action $action
Assert-True ($script:CreateCalls -eq 1) 'normal-publication-one-create'
Assert-True ([int]$published.issue_number -eq 501) 'normal-publication-binds-issue'
Assert-True ([string]$published.publication_state -ceq 'PUBLISHED') 'normal-publication-state'

$taskLost=New-Task -Id 'task-lost' -Generation 3
$actionLost=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='act-lost';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
$leaseLost=New-V4Lease -Task $taskLost -Action $actionLost;$taskLost.lease=$leaseLost
$script:CreateCalls=0;$script:FakeIssues=@()
$githubLost=[pscustomobject]@{
    CreateIssue={param($title,$body)$script:CreateCalls++;$script:FakeIssues+=,[pscustomobject]@{number=502;title=$title;body=$body};throw 'SIMULATED_RESPONSE_LOST'}
    SearchIssues={param($identity)@($script:FakeIssues)}
    ReadIssue={param($number)@($script:FakeIssues|Where-Object{[int]$_.number-eq[int]$number})[0]}
    ReadComments={param($number)@()}
}
$lost=Publish-V4Action -Context ([pscustomobject]@{GitHub=$githubLost;TrustedActor='Scorp96'}) -Task $taskLost -Lease $leaseLost -Action $actionLost
Assert-True ($script:CreateCalls -eq 1) 'lost-response-never-retries-create'
Assert-True ([int]$lost.issue_number -eq 502) 'lost-response-reconciles-existing-issue'
Assert-True ([string]$lost.publication_state -ceq 'RECONCILED') 'lost-response-reconciled-state'

$taskZero=New-Task -Id 'task-zero' -Generation 1
$actionZero=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='act-zero';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
$leaseZero=New-V4Lease -Task $taskZero -Action $actionZero;$taskZero.lease=$leaseZero
$githubZero=[pscustomobject]@{CreateIssue={param($t,$b)throw 'LOST'};SearchIssues={param($i)@()};ReadIssue={param($n)$null};ReadComments={param($n)@()}}
$zeroFailed=$false
try{[void](Publish-V4Action -Context ([pscustomobject]@{GitHub=$githubZero;TrustedActor='Scorp96'}) -Task $taskZero -Lease $leaseZero -Action $actionZero)}catch{$zeroFailed=$_.Exception.Message -match 'AMBIGUOUS_PUBLICATION'}
Assert-True $zeroFailed 'zero-match-publication-fails-closed'

$taskDup=New-Task -Id 'task-dup' -Generation 1
$actionDup=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='act-dup';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
$leaseDup=New-V4Lease -Task $taskDup -Action $actionDup;$taskDup.lease=$leaseDup
$binding=Get-V4BindingText -Lease $leaseDup -Action $actionDup
$script:FakeIssues=@([pscustomobject]@{number=601;title='x';body=$binding},[pscustomobject]@{number=602;title='x';body=$binding})
$githubDup=[pscustomobject]@{CreateIssue={param($t,$b)throw 'LOST'};SearchIssues={param($i)@($script:FakeIssues)};ReadIssue={param($n)$null};ReadComments={param($n)@()}}
$dupFailed=$false
try{[void](Publish-V4Action -Context ([pscustomobject]@{GitHub=$githubDup;TrustedActor='Scorp96'}) -Task $taskDup -Lease $leaseDup -Action $actionDup)}catch{$dupFailed=$_.Exception.Message -match 'DUPLICATE_V4_ISSUE'}
Assert-True $dupFailed 'duplicate-v4-issue-fails-closed'

$taskLife=New-Task -Id 'task-life' -Generation 4
$actionLife=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='act-life';action_kind='health';timeout_seconds=60;safety_class='standard';payload=[pscustomobject]@{}}
$leaseLife=New-V4Lease -Task $taskLife -Action $actionLife;$leaseLife.issue_number=701;$claim='abc123claim'
$script:LifeComments=@(
    [pscustomobject]@{author='Scorp96';body="SCORP_EXEC_CLAIMED`nProtocol: scorp.exec/v4`nIssue: 701`nTaskId: $($leaseLife.v4_task_id)`nActionId: act-life`nClaimToken: $claim"},
    [pscustomobject]@{author='Scorp96';body="SCORP_EXEC_RESULT`nProtocol: scorp.exec/v4`nIssue: 701`nTaskId: $($leaseLife.v4_task_id)`nActionId: act-life`nClaimToken: $claim`nStatus: SUCCEEDED`nExitCode: 0`nResultSHA256: $('a'*64)"}
)
$githubLife=[pscustomobject]@{CreateIssue={};SearchIssues={};ReadIssue={param($n)[pscustomobject]@{number=701;title='done';body=''}};ReadComments={param($n)@($script:LifeComments)}}
$ctxLife=[pscustomobject]@{GitHub=$githubLife;TrustedActor='Scorp96'}
$life=Read-V4Lifecycle -Context $ctxLife -Lease $leaseLife
Assert-True ([string]$life.claim_token -ceq $claim) 'lifecycle-claim-bound'
Assert-True ([string]$life.status -ceq 'SUCCEEDED') 'lifecycle-terminal-status'
Assert-True ([string]$life.result_sha256 -ceq ('a'*64)) 'lifecycle-result-hash'
$script:LifeComments[1].body=$script:LifeComments[1].body.Replace("TaskId: $($leaseLife.v4_task_id)",'TaskId: wrong-task')
$mismatchFailed=$false
try{[void](Read-V4Lifecycle -Context $ctxLife -Lease $leaseLife)}catch{$mismatchFailed=$_.Exception.Message -match 'LIFECYCLE_IDENTITY_MISMATCH'}
Assert-True $mismatchFailed 'lifecycle-identity-mismatch-fails-closed'
$script:LifeComments[1].body=$script:LifeComments[1].body.Replace('TaskId: wrong-task',"TaskId: $($leaseLife.v4_task_id)")
$taskLife.lease=$leaseLife
$first=Reconcile-V4Lease -Context $ctxLife -Task $taskLife
Assert-True ([string]$first.status -ceq 'TERMINAL') 'reconcile-terminal-first'
Assert-True ([bool]$taskLife.lease.terminal_applied) 'reconcile-marks-terminal-applied'
$second=Reconcile-V4Lease -Context $ctxLife -Task $taskLife
Assert-True ([string]$second.status -ceq 'ALREADY_APPLIED') 'reconcile-terminal-once'
Write-Output 'ORCHESTRATOR_V1_ROUTER_PASS'

# ---------------- Continuation transport / decision binding ----------------
if(-not(Test-Path -LiteralPath $ContinuationPath -PathType Leaf)){throw "ORCH_CONTINUATION_MODULE_MISSING: $ContinuationPath"}
. $ContinuationPath
foreach($fn in @('New-ContinuationRequest','Publish-ContinuationRequest','Read-ContinuationDecision','Apply-ContinuationDecision','Finalize-ContinuationRequest')){
    Assert-True ($null-ne(Get-Command $fn -CommandType Function -ErrorAction SilentlyContinue)) ("function-"+$fn)
}

$contRoot=Join-Path $env:TEMP ('scorp-orch-cont-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $contRoot|Out-Null
try{
    $result=[pscustomobject]@{action_id='a-001';result_sha256=('b'*64);status='SUCCEEDED'}
    $contTask=New-Task -Id 'cont-task' -Generation 0
    $contTask.state='WAITING';$contTask.waiting_reason='GPT_CONTINUATION_REQUIRED';$contTask.last_result=$result
    $request=New-ContinuationRequest -Task $contTask -Result $result -Question 'Choose the next exact deterministic action.' -RegistrySequence 7
    Assert-True ([string]$request.protocol_version -ceq 'scorp.orchestrator/continuation-request-v1') 'continuation-request-protocol'
    Assert-True ([string]$request.task_id -ceq 'cont-task') 'continuation-request-task-bound'
    Assert-True ([int64]$request.registry_sequence -eq 7) 'continuation-request-sequence-bound'
    Assert-True ([string]$request.previous_action_id -ceq 'a-001' -and [string]$request.previous_result_sha256 -ceq ('b'*64)) 'continuation-request-result-bound'
    Assert-True (-not[string]::IsNullOrWhiteSpace([string]$request.request_id)) 'continuation-request-has-id'

    $script:ContIssues=@();$script:ContCreateCalls=0;$script:OutboxSeen=$false;$script:ContComments=@();$script:UpdateCalls=0
    $expectedOutbox=Join-Path (Join-Path $contRoot 'continuation-outbox') ($request.request_id+'.json')
    $githubCont=[pscustomobject]@{
        CreateIssue={param($title,$body)$script:ContCreateCalls++;$script:OutboxSeen=Test-Path -LiteralPath $expectedOutbox;$i=[pscustomobject]@{number=801;title=$title;body=$body};$script:ContIssues+=,$i;return $i}
        SearchIssues={param($identity)@($script:ContIssues)}
        ReadIssue={param($number)@($script:ContIssues|Where-Object{[int]$_.number-eq[int]$number})[0]}
        ReadComments={param($number)@($script:ContComments)}
        UpdateIssue={param($number,$title,$state)$script:UpdateCalls++;[pscustomobject]@{number=$number;title=$title;state=$state}}
    }
    $ctxCont=[pscustomobject]@{Root=$contRoot;GitHub=$githubCont;TrustedActor='Scorp96'}
    $publishedRequest=Publish-ContinuationRequest -Context $ctxCont -Request $request
    Assert-True $script:OutboxSeen 'continuation-outbox-before-create'
    Assert-True ($script:ContCreateCalls -eq 1) 'continuation-exactly-one-create'
    Assert-True ([string]$script:ContIssues[0].title -cmatch '^\[SCORP_CONT_REQ\]') 'continuation-request-title'
    Assert-True ([string]$script:ContIssues[0].title -cnotmatch '\[SCORP_EXEC\]') 'continuation-never-exec-title'
    Assert-True ([int]$publishedRequest.issue_number -eq 801) 'continuation-request-issue-bound'
    [void](Publish-ContinuationRequest -Context $ctxCont -Request $publishedRequest)
    Assert-True ($script:ContCreateCalls -eq 1) 'continuation-republish-idempotent'

    $decision=[pscustomobject]@{
        protocol_version='scorp.orchestrator/continuation-decision-v1';decision_id='d-001';request_id=[string]$request.request_id;task_id='cont-task';expected_registry_sequence=7;previous_action_id='a-001';previous_result_sha256=('b'*64);previous_continuation_generation=0;created_at='2026-09-10T02:00:00Z';expires_at='2026-09-11T00:00:00Z';mutation=[pscustomobject]@{kind='SET_NEXT_ACTION';next_action=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='a-002';action_kind='health'}}
    }
    $decisionBody="SCORP_CONT_DECISION`n"+($decision|ConvertTo-Json -Depth 64 -Compress)
    $script:ContComments=@([pscustomobject]@{author='OtherUser';body=$decisionBody},[pscustomobject]@{author='Scorp96';body=$decisionBody})
    $read=Read-ContinuationDecision -Context $ctxCont -Request $publishedRequest
    Assert-True ([string]$read.status -ceq 'DECISION') 'trusted-decision-selected'
    Assert-True ([string]$read.decision.decision_id -ceq 'd-001') 'decision-id-bound'
    Assert-True ([string]$read.decision.request_id -ceq [string]$request.request_id) 'decision-request-bound'
    Assert-True ([string]$read.decision.previous_result_sha256 -ceq ('b'*64)) 'decision-result-bound'

    $script:ContComments+=,[pscustomobject]@{author='Scorp96';body=$decisionBody}
    $dupDecision=$false
    try{[void](Read-ContinuationDecision -Context $ctxCont -Request $publishedRequest)}catch{$dupDecision=$_.Exception.Message -match 'DUPLICATE_CONTINUATION_DECISION'}
    Assert-True $dupDecision 'duplicate-decision-fails-closed'
    $script:ContComments=@([pscustomobject]@{author='Scorp96';body=$decisionBody})

    $contTask.continuation_request_id=[string]$request.request_id
    $registry=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=7;tasks=@($contTask);updated_at='2026-09-10T02:00:00Z'}
    Write-RegistrySnapshot -Root $contRoot -Registry $registry
    $stale=$decision|ConvertTo-Json -Depth 64|ConvertFrom-Json;$stale.expected_registry_sequence=6;$stale.decision_id='d-stale'
    $staleFailed=$false
    try{[void](Apply-ContinuationDecision -Root $contRoot -Task $contTask -Decision $stale)}catch{$staleFailed=$_.Exception.Message -match 'STALE_CONTINUATION_DECISION'}
    Assert-True $staleFailed 'stale-decision-fails-closed'

    $applied=Apply-ContinuationDecision -Root $contRoot -Task $contTask -Decision $decision
    Assert-True ([string]$applied.status -ceq 'APPLIED') 'decision-applied'
    $inboxPath=Join-Path (Join-Path $contRoot 'continuation-inbox') ($request.request_id+'.json')
    Assert-True (Test-Path -LiteralPath $inboxPath -PathType Leaf) 'decision-inbox-artifact-written'
    $inboxBytes=[IO.File]::ReadAllBytes($inboxPath)
    Assert-True (-not ($inboxBytes.Length-ge3 -and $inboxBytes[0]-eq0xEF -and $inboxBytes[1]-eq0xBB -and $inboxBytes[2]-eq0xBF)) 'decision-inbox-utf8-no-bom'
    $inbox=Get-Content -LiteralPath $inboxPath -Raw -Encoding UTF8|ConvertFrom-Json
    Assert-True ([string]$inbox.decision_id -ceq 'd-001' -and [string]$inbox.request_id -ceq [string]$request.request_id) 'decision-inbox-identity-bound'
    $after=Read-RegistrySnapshot -Root $contRoot
    Assert-True ([int64]$after.sequence -eq 8) 'decision-increments-registry-sequence'
    $afterTask=@($after.tasks|Where-Object task_id -EQ 'cont-task')[0]
    Assert-True ([string]$afterTask.state -ceq 'READY' -and [string]$afterTask.next_action.action_id -ceq 'a-002') 'decision-sets-next-action-ready'
    Assert-True ([int64]$afterTask.generation -eq 1) 'decision-increments-task-generation'
    $appliedAgain=Apply-ContinuationDecision -Root $contRoot -Task $afterTask -Decision $decision
    $afterAgain=Read-RegistrySnapshot -Root $contRoot
    Assert-True ([string]$appliedAgain.status -ceq 'ALREADY_APPLIED') 'decision-apply-idempotent'
    Assert-True ([int64]$afterAgain.sequence -eq 8) 'decision-retry-no-sequence-growth'

    $githubCleanupLost=[pscustomobject]@{
        CreateIssue=$githubCont.CreateIssue;SearchIssues=$githubCont.SearchIssues;ReadIssue=$githubCont.ReadIssue;ReadComments=$githubCont.ReadComments;UpdateIssue={param($number,$title,$state)throw 'SIMULATED_CLEANUP_ACK_LOST'}
    }
    $cleanup=Finalize-ContinuationRequest -Context ([pscustomobject]@{Root=$contRoot;GitHub=$githubCleanupLost;TrustedActor='Scorp96'}) -Request $publishedRequest -Decision $decision
    Assert-True ([string]$cleanup.status -ceq 'CLEANUP_PENDING') 'cleanup-ack-loss-is-pending'
    $third=Apply-ContinuationDecision -Root $contRoot -Task $afterTask -Decision $decision
    $afterThird=Read-RegistrySnapshot -Root $contRoot
    Assert-True ([string]$third.status -ceq 'ALREADY_APPLIED' -and [int64]$afterThird.sequence -eq 8) 'cleanup-retry-never-reapplies-decision'
}finally{
    Remove-Item -LiteralPath $contRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output 'ORCHESTRATOR_V1_ROUTER_CONTINUATION_PASS'
