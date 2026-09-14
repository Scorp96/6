param(
    [Parameter(Mandatory=$true)][string]$StorePath,
    [Parameter(Mandatory=$true)][string]$RegistryPath,
    [Parameter(Mandatory=$true)][string]$HealthPath,
    [Parameter(Mandatory=$true)][string]$EntrypointPath
)

$ErrorActionPreference='Stop'
Set-StrictMode -Version 2.0

function Assert-True {
    param([bool]$Condition,[string]$Name)
    if(-not$Condition){throw "ORCHESTRATOR_V1_ACCEPTANCE_FAIL: $Name"}
    Write-Output "PASS $Name"
}

. $StorePath
. $RegistryPath
. $HealthPath
. $EntrypointPath -LibraryMode

$root=Join-Path $env:TEMP ('orch-accept-contract-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root|Out-Null
try {
    $now=[DateTimeOffset]::UtcNow.ToString('o')
    $action=[pscustomobject]@{
        protocol_version='scorp.exec/v4'
        action_id='accept-health-terminal-001'
        action_kind='health'
        timeout_seconds=30
        safety_class='standard'
        payload=[pscustomobject]@{}
    }
    $lease=[pscustomobject]@{
        protocol_version='scorp.orchestrator/v4-lease-v1'
        lease_id=('a'*64)
        orchestrator_task_id='accept-terminal'
        orchestrator_generation=0
        v4_task_id='orch/accept-terminal/g0'
        v4_action_id='accept-health-terminal-001'
        envelope_hash=('b'*64)
        issue_number=999999
        claim_token='accept-claim'
        publication_state='PUBLISHED'
        terminal_applied=$false
        terminal_result_sha256=$null
        terminal_status=$null
    }
    $task=[pscustomobject]@{
        protocol_version='scorp.orchestrator/task-v1'
        task_id='accept-terminal'
        project_id='orchestrator-v1-acceptance'
        priority=100
        state='RUNNING'
        dependencies=@()
        dependency_policy='all_done'
        parallel_safety=$false
        execution_adapter='LOCAL_V4'
        next_action=$action
        continuation_request_id=$null
        checkpoint=[pscustomobject]@{terminal_on_success=$true}
        attempts=1
        max_attempts=1
        next_eligible_at=$null
        created_at=$now
        updated_at=$now
        last_result=$null
        evidence_refs=@()
        safety_class='standard'
        authorization_requirement=$null
        waiting_reason=$null
        blocker=$null
        lease=$lease
        generation=0
        resource_scope=@('acceptance-terminal')
    }
    $registry=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@($task);updated_at=$now}
    Write-RegistrySnapshot -Root $root -Registry $registry
    $context=[pscustomobject]@{Root=$root;Now=[DateTimeOffset]::UtcNow}
    $terminal=[pscustomobject]@{terminal_status='SUCCEEDED';result_sha256=('c'*64);exit_code=0}
    $result=Complete-OrchestratorV4Result -Context $context -Task $task -Terminal $terminal
    $after=Read-RegistrySnapshot -Root $root
    $live=@($after.tasks|Where-Object{[string]$_.task_id-ceq'accept-terminal'})[0]
    Assert-True ([string]$live.state-ceq'DONE') 'preauthored-terminal-success-done'
    Assert-True ($null-eq$live.next_action) 'terminal-success-clears-next-action'
    Assert-True ([string]::IsNullOrWhiteSpace([string]$live.waiting_reason)) 'terminal-success-no-gpt-wait'
    Assert-True ([string]$result.mapped_state-ceq'DONE') 'terminal-success-result-mapped-done'
    Write-Output 'ORCHESTRATOR_V1_ACCEPTANCE_CONTRACT_PASS'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output '--- checkpoint-boundary-contract ---'
$boundaryCommand=Get-Command Get-CheckpointBoundaryViolations -ErrorAction SilentlyContinue
Assert-True ($null-ne$boundaryCommand) 'checkpoint-boundary-detector-defined'
$boundaryRoot=Join-Path $env:TEMP ('orch-accept-boundary-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $boundaryRoot|Out-Null
try {
    $boundaryNow=[DateTimeOffset]::UtcNow
    $deadline=$boundaryNow.AddMinutes(-1).ToString('o')
    $waitTask=[pscustomobject]@{
        protocol_version='scorp.orchestrator/task-v1';task_id='accept-boundary';project_id='orchestrator-v1-acceptance';priority=10;state='WAITING';dependencies=@();dependency_policy='all_done';parallel_safety=$false;execution_adapter='LOCAL_V4';next_action=$null;continuation_request_id=$null;checkpoint=[pscustomobject]@{deadline_at=$deadline};attempts=0;max_attempts=1;next_eligible_at=$null;created_at=$boundaryNow.AddMinutes(-31).ToString('o');updated_at=$boundaryNow.AddMinutes(-31).ToString('o');last_result=$null;evidence_refs=@();safety_class='standard';authorization_requirement=$null;waiting_reason='CHECKPOINT_TEST_WAIT';blocker=$null;lease=$null;generation=0;resource_scope=@('acceptance-boundary')
    }
    $boundaryRegistry=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@($waitTask);updated_at=$boundaryNow.ToString('o')}
    Write-RegistrySnapshot -Root $boundaryRoot -Registry $boundaryRegistry
    $tick=Invoke-OrchestratorTickSafe -Context ([pscustomobject]@{Root=$boundaryRoot;Now=$boundaryNow})
    $afterBoundary=Read-RegistrySnapshot -Root $boundaryRoot
    $health=Get-Content (Join-Path $boundaryRoot 'health.json') -Raw|ConvertFrom-Json
    Assert-True ([int64]$afterBoundary.sequence-eq0) 'checkpoint-boundary-observational-no-registry-mutation'
    Assert-True ([string]$health.status-ceq'CHECKPOINT_BOUNDARY_VIOLATION') 'checkpoint-boundary-health-status'
    Assert-True ((Test-HasProperty $health 'checkpoint_boundary_violations') -and @($health.checkpoint_boundary_violations).Count-eq1) 'checkpoint-boundary-one-violation'
    Assert-True ([string]$health.checkpoint_boundary_violations[0].task_id-ceq'accept-boundary') 'checkpoint-boundary-task-bound'
    Assert-True ([string]$health.checkpoint_boundary_violations[0].reason-ceq'CHECKPOINT_DEADLINE_EXPIRED') 'checkpoint-boundary-reason'
    Assert-True ([string]$afterBoundary.tasks[0].checkpoint.deadline_at-ceq$deadline) 'checkpoint-boundary-does-not-invent-checkpoint'
    Write-Output 'ORCHESTRATOR_V1_CHECKPOINT_BOUNDARY_PASS'
} finally {
    Remove-Item -LiteralPath $boundaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}
