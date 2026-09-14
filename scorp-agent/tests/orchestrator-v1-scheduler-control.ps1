param(
    [Parameter(Mandatory=$true)][string]$StorePath,
    [Parameter(Mandatory=$true)][string]$RegistryPath,
    [Parameter(Mandatory=$true)][string]$HealthPath,
    [Parameter(Mandatory=$true)][string]$EntrypointPath
)

$ErrorActionPreference='Stop'

function Assert-True {
    param([bool]$Condition,[string]$Name)
    if(-not $Condition){throw "ORCHESTRATOR_V1_SCHEDULER_CONTROL_FAIL: $Name"}
    Write-Output "PASS $Name"
}

if(-not(Test-Path -LiteralPath $RegistryPath -PathType Leaf)){throw "ORCH_REGISTRY_MODULE_MISSING: $RegistryPath"}
if(-not(Test-Path -LiteralPath $HealthPath -PathType Leaf)){throw "ORCH_HEALTH_MODULE_MISSING: $HealthPath"}
if(-not(Test-Path -LiteralPath $EntrypointPath -PathType Leaf)){throw "ORCH_ENTRYPOINT_MISSING: $EntrypointPath"}
if(-not(Test-Path -LiteralPath $StorePath -PathType Leaf)){throw "ORCH_STORE_MISSING: $StorePath"}

. $StorePath
. $RegistryPath
. $HealthPath
. $EntrypointPath -LibraryMode

foreach($fn in @('Invoke-RegistryMutation','Get-RunnableTasks','Select-NextTask','Get-ControlState','Set-HealthState','Get-OrchestratorMutexName','Try-AcquireOrchestratorMutex','Invoke-OrchestratorTick')){
    Assert-True ($null-ne(Get-Command $fn -CommandType Function -ErrorAction SilentlyContinue)) ("function-"+$fn)
}

function New-TestTask {
    param(
        [string]$Id,
        [int]$Priority=100,
        [string]$State='READY',
        $NextAction=$null,
        [string[]]$Dependencies=@(),
        [string[]]$ResourceScope=@(),
        $Lease=$null,
        [string]$NextEligibleAt=$null,
        [int]$Generation=0
    )
    return [pscustomobject]@{
        protocol_version='scorp.orchestrator/task-v1';task_id=$Id;project_id='p';priority=$Priority;state=$State;dependencies=@($Dependencies);dependency_policy='all_done';parallel_safety=$false;execution_adapter='LOCAL_V4';next_action=$NextAction;continuation_request_id=$null;checkpoint=$null;attempts=0;max_attempts=3;next_eligible_at=$NextEligibleAt;created_at='2026-09-10T00:00:00Z';updated_at='2026-09-10T00:00:00Z';last_result=$null;evidence_refs=@();safety_class='standard';authorization_requirement=$null;waiting_reason=$null;blocker=$null;lease=$Lease;generation=$Generation;resource_scope=@($ResourceScope)
    }
}

$now=[DateTimeOffset]::Parse('2026-09-10T01:00:00Z')
$a1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='a1';action_kind='health'}
$b1=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='b1';action_kind='health'}

$reg=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@(
    (New-TestTask -Id 'b' -NextAction $b1),
    (New-TestTask -Id 'a' -NextAction $a1)
)}
$selected=Select-NextTask -Registry $reg -Now $now
Assert-True ([string]$selected.task_id -ceq 'a') 'stable-task-id-tiebreak'

$regPriority=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@(
    (New-TestTask -Id 'a' -Priority 100 -NextAction $a1),
    (New-TestTask -Id 'z' -Priority 200 -NextAction $b1)
)}
Assert-True ([string](Select-NextTask -Registry $regPriority -Now $now).task_id -ceq 'z') 'priority-ordering'

$missing=New-TestTask -Id 'missing-action' -NextAction $null
$regMissing=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@($missing)}
Assert-True (@(Get-RunnableTasks -Registry $regMissing -Now $now).Count -eq 0) 'no-local-reasoning'

$future=New-TestTask -Id 'future' -NextAction $a1 -NextEligibleAt '2026-09-10T02:00:00Z'
$regFuture=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@($future)}
Assert-True (@(Get-RunnableTasks -Registry $regFuture -Now $now).Count -eq 0) 'next-eligible-gate'

$dep=New-TestTask -Id 'dep' -State 'WAITING' -NextAction $null
$child=New-TestTask -Id 'child' -NextAction $a1 -Dependencies @('dep')
$regDep=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@($dep,$child)}
Assert-True (@(Get-RunnableTasks -Registry $regDep -Now $now|Where-Object task_id -EQ 'child').Count -eq 0) 'dependency-gate'
$dep.state='DONE'
Assert-True (@(Get-RunnableTasks -Registry $regDep -Now $now|Where-Object task_id -EQ 'child').Count -eq 1) 'dependency-done-allows-run'

$running=New-TestTask -Id 'running' -State 'RUNNING' -NextAction $null -ResourceScope @('file:C:\\shared') -Lease ([pscustomobject]@{lease_id='l1';action_id='r1'})
$conflict=New-TestTask -Id 'conflict' -NextAction $a1 -ResourceScope @('file:C:\\shared')
$safe=New-TestTask -Id 'safe' -NextAction $b1 -ResourceScope @('file:C:\\other')
$regScope=[pscustomobject]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@($running,$conflict,$safe)}
$runnable=@(Get-RunnableTasks -Registry $regScope -Now $now)
Assert-True (@($runnable|Where-Object task_id -EQ 'conflict').Count -eq 0) 'resource-conflict-gate'
Assert-True (@($runnable|Where-Object task_id -EQ 'safe').Count -eq 1) 'resource-nonconflict-allows-run'

$root=Join-Path $env:TEMP ('scorp-orch-scheduler-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root|Out-Null
try{
    $runLease=[pscustomobject]@{lease_id='lease-running';action_id='already-running';issue_number=123}
    $runTask=New-TestTask -Id 'run-task' -State 'RUNNING' -Lease $runLease -ResourceScope @('file:C:\\run')
    $readyTask=New-TestTask -Id 'ready-task' -NextAction $a1 -ResourceScope @('file:C:\\ready')
    $registry=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@($runTask,$readyTask);updated_at='2026-09-10T01:00:00Z'}
    Write-RegistrySnapshot -Root $root -Registry $registry
    Write-Utf8NoBomAtomic -Path (Join-Path $root 'control.json') -Text (([ordered]@{protocol_version='scorp.orchestrator/control-v1';paused=$true;emergency_stop=$false;updated_at='2026-09-10T01:00:00Z'}|ConvertTo-Json -Depth 16))
    $tick=Invoke-OrchestratorTick -Context ([pscustomobject]@{Root=$root;Now=$now})
    Assert-True ([string]$tick.status -ceq 'PAUSED') 'pause-prevents-new-launch'
    $afterPause=Read-RegistrySnapshot -Root $root
    Assert-True ([int64]$afterPause.sequence -eq 0) 'pause-does-not-mutate-registry'
    $preserved=@($afterPause.tasks|Where-Object task_id -EQ 'run-task')[0]
    Assert-True ([string]$preserved.lease.lease_id -ceq 'lease-running') 'running-lease-preserved-under-pause'

    Write-Utf8NoBomAtomic -Path (Join-Path $root 'control.json') -Text (([ordered]@{protocol_version='scorp.orchestrator/control-v1';paused=$false;emergency_stop=$false;updated_at='2026-09-10T01:01:00Z'}|ConvertTo-Json -Depth 16))
    $waitTask=New-TestTask -Id 'needs-gpt' -NextAction $null
    $registry2=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=10;tasks=@($waitTask);updated_at='2026-09-10T01:01:00Z'}
    Write-RegistrySnapshot -Root $root -Registry $registry2
    Remove-Item -LiteralPath (Join-Path $root 'journal.jsonl') -Force -ErrorAction SilentlyContinue
    $tick1=Invoke-OrchestratorTick -Context ([pscustomobject]@{Root=$root;Now=$now})
    Assert-True ([string]$tick1.status -ceq 'WAITING_GPT') 'missing-action-transitions-waiting'
    $after1=Read-RegistrySnapshot -Root $root
    $needs=@($after1.tasks|Where-Object task_id -EQ 'needs-gpt')[0]
    Assert-True ([string]$needs.state -ceq 'WAITING' -and [string]$needs.waiting_reason -ceq 'GPT_CONTINUATION_REQUIRED') 'missing-action-waiting-reason'
    Assert-True ([int64]$after1.sequence -eq 11) 'missing-action-single-mutation'
    $tick2=Invoke-OrchestratorTick -Context ([pscustomobject]@{Root=$root;Now=$now})
    $after2=Read-RegistrySnapshot -Root $root
    Assert-True ([string]$tick2.status -ceq 'IDLE') 'unchanged-waiting-idle'
    Assert-True ([int64]$after2.sequence -eq 11) 'unchanged-generation-no-spin'

    Set-HealthState -Root $root -Health ([ordered]@{protocol_version='scorp.orchestrator/health-v1';status='OK';heartbeat_at='2026-09-10T01:02:00Z';queue_depth=1})
    $health=Get-Content -LiteralPath (Join-Path $root 'health.json') -Raw|ConvertFrom-Json
    Assert-True ([string]$health.status -ceq 'OK' -and [int]$health.queue_depth -eq 1) 'health-state-roundtrip'

    $defaultName=Get-OrchestratorMutexName
    Assert-True ([string]$defaultName -cmatch '^Global\\ScorpFullAutoOrchestratorV1-') 'mutex-default-global-name'
    $probeName='Local\ScorpFullAutoOrchestratorV1-Test-'+[guid]::NewGuid().ToString('N')
    $m=New-Object Threading.Mutex($false,$probeName)
    try{
        Assert-True ($m.WaitOne(0)) 'mutex-test-fixture-acquired'
        $out=& ($env:SystemRoot+'\\System32\\WindowsPowerShell\\v1.0\\powershell.exe') -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $EntrypointPath -MutexProbe -MutexNameOverride $probeName 2>&1
        $code=$LASTEXITCODE
        Assert-True ($code -eq 0 -and (($out-join'`n') -match 'MUTEX_DENIED')) 'mutex-denies-second-instance'
    }finally{
        try{$m.ReleaseMutex()}catch{}
        $m.Dispose()
    }
}finally{
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output 'ORCHESTRATOR_V1_SCHEDULER_CONTROL_PASS'
