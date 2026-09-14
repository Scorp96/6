param(
    [Parameter(Mandatory=$true)][string]$TaskSchemaPath,
    [Parameter(Mandatory=$true)][string]$ContinuationSchemaPath,
    [Parameter(Mandatory=$true)][string]$StorePath
)

$ErrorActionPreference='Stop'

function Assert-True {
    param([bool]$Condition,[string]$Name)
    if(-not $Condition){throw "ORCHESTRATOR_V1_CONTRACT_STORE_FAIL: $Name"}
    Write-Output "PASS $Name"
}

if(-not(Test-Path -LiteralPath $TaskSchemaPath -PathType Leaf)){throw "ORCH_TASK_SCHEMA_MISSING: $TaskSchemaPath"}
if(-not(Test-Path -LiteralPath $ContinuationSchemaPath -PathType Leaf)){throw "ORCH_CONTINUATION_SCHEMA_MISSING: $ContinuationSchemaPath"}
if(-not(Test-Path -LiteralPath $StorePath -PathType Leaf)){throw "ORCH_STORE_MISSING: $StorePath"}

$taskSchema=Get-Content -LiteralPath $TaskSchemaPath -Raw|ConvertFrom-Json
$required=@($taskSchema.required)
foreach($name in @('protocol_version','task_id','project_id','priority','state','dependencies','dependency_policy','parallel_safety','execution_adapter','next_action','attempts','max_attempts','created_at','updated_at','evidence_refs','safety_class','generation','resource_scope')){
    Assert-True ($required -contains $name) ("task-required-"+$name)
}
Assert-True ([string]$taskSchema.properties.task_id.type -ceq 'string') 'task-id-string'
Assert-True (@($taskSchema.properties.state.enum) -contains 'READY') 'task-state-ready'
Assert-True (@($taskSchema.properties.state.enum) -contains 'WAITING') 'task-state-waiting'
Assert-True (@($taskSchema.properties.state.enum) -contains 'BLOCKED') 'task-state-blocked'
Assert-True ($null-ne$taskSchema.properties.next_action) 'task-next-action-defined'
Assert-True ($null-ne$taskSchema.properties.lease) 'task-lease-defined'
Assert-True (@($taskSchema.properties.execution_adapter.enum) -contains 'LOCAL_V4') 'task-adapter-local-v4'
Assert-True (@($taskSchema.properties.execution_adapter.enum) -contains 'GPT_HOST') 'task-adapter-gpt-host'
Assert-True ([string]$taskSchema.properties.generation.type -ceq 'integer') 'task-generation-integer'
Assert-True ([string]$taskSchema.properties.resource_scope.type -ceq 'array') 'task-resource-scope-array'

$contSchema=Get-Content -LiteralPath $ContinuationSchemaPath -Raw|ConvertFrom-Json
Assert-True ($null-ne$contSchema.definitions.request) 'continuation-request-defined'
Assert-True ($null-ne$contSchema.definitions.decision) 'continuation-decision-defined'
$decisionRequired=@($contSchema.definitions.decision.required)
foreach($name in @('protocol_version','decision_id','request_id','task_id','expected_registry_sequence','previous_continuation_generation','mutation')){
    Assert-True ($decisionRequired -contains $name) ("decision-required-"+$name)
}
Assert-True ([string]$contSchema.definitions.decision.properties.decision_id.type -ceq 'string') 'decision-id-string'
Assert-True ([string]$contSchema.definitions.decision.properties.expected_registry_sequence.type -ceq 'integer') 'decision-generation-bound'

. $StorePath
foreach($fn in @('Write-Utf8NoBomAtomic','Get-Sha256Text','Append-WalRecord','Read-RegistrySnapshot','Write-RegistrySnapshot','Recover-RegistryFromWal','Assert-TaskRecord','Assert-ContinuationRequest','Assert-ContinuationDecision')){
    Assert-True ($null-ne(Get-Command $fn -CommandType Function -ErrorAction SilentlyContinue)) ("function-"+$fn)
}

$root=Join-Path $env:TEMP ('scorp-orch-store-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root|Out-Null
try{
    $atomicPath=Join-Path $root 'atomic.json'
    Write-Utf8NoBomAtomic -Path $atomicPath -Text '{"x":1}'
    $bytes=[IO.File]::ReadAllBytes($atomicPath)
    Assert-True (-not($bytes.Length-ge3 -and $bytes[0]-eq0xEF -and $bytes[1]-eq0xBB -and $bytes[2]-eq0xBF)) 'utf8-no-bom'
    Assert-True ((Get-Content -LiteralPath $atomicPath -Raw) -ceq '{"x":1}') 'atomic-first-write'
    Write-Utf8NoBomAtomic -Path $atomicPath -Text '{"x":2}'
    Assert-True ((Get-Content -LiteralPath $atomicPath -Raw) -ceq '{"x":2}') 'atomic-replace'

    $hashA=Get-Sha256Text 'abc'
    $hashB=Get-Sha256Text 'abc'
    $hashC=Get-Sha256Text 'abcd'
    Assert-True ($hashA -cmatch '^[0-9a-f]{64}$') 'sha256-format'
    Assert-True ($hashA -ceq $hashB -and $hashA -cne $hashC) 'sha256-deterministic'

    $reg0=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=0;tasks=@();updated_at='2026-09-10T00:00:00Z'}
    Write-RegistrySnapshot -Root $root -Registry $reg0
    $loaded=Read-RegistrySnapshot -Root $root
    Assert-True ([int]$loaded.sequence -eq 0) 'snapshot-roundtrip'

    $reg1=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=1;tasks=@();updated_at='2026-09-10T00:01:00Z'}
    Append-WalRecord -Root $root -Record ([ordered]@{protocol_version='scorp.orchestrator/wal-v1';sequence=1;prior_sequence=0;mutation='TEST';registry=$reg1})
    $recovered=Recover-RegistryFromWal -Root $root
    Assert-True ([int]$recovered.sequence -eq 1) 'wal-replay-one-record'

    $walPath=Join-Path $root 'journal.jsonl'
    $bad=[ordered]@{protocol_version='scorp.orchestrator/wal-v1';sequence=3;prior_sequence=1;mutation='BAD_FORK';registry=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=3;tasks=@();updated_at='2026-09-10T00:03:00Z'}}
    $badLine=($bad|ConvertTo-Json -Depth 64 -Compress)+[Environment]::NewLine
    [IO.File]::AppendAllText($walPath,$badLine,(New-Object Text.UTF8Encoding($false)))
    $forkFailed=$false
    try{[void](Recover-RegistryFromWal -Root $root)}catch{$forkFailed=$true;Assert-True ($_.Exception.Message -match 'WAL_(SEQUENCE|PRIOR)_MISMATCH') 'wal-fork-error-specific'}
    Assert-True $forkFailed 'wal-fork-fails-closed'

    $validTask=[pscustomobject]@{
        protocol_version='scorp.orchestrator/task-v1';task_id='task-a';project_id='p';priority=100;state='READY';dependencies=@();dependency_policy='all_done';parallel_safety=$false;execution_adapter='LOCAL_V4';next_action=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='a1';action_kind='health'};continuation_request_id=$null;checkpoint=$null;attempts=0;max_attempts=1;next_eligible_at=$null;created_at='2026-09-10T00:00:00Z';updated_at='2026-09-10T00:00:00Z';last_result=$null;evidence_refs=@();safety_class='standard';authorization_requirement=$null;waiting_reason=$null;blocker=$null;lease=$null;generation=0;resource_scope=@()
    }
    Assert-TaskRecord $validTask
    Write-Output 'PASS task-record-runtime-validation'

    $badTask=$validTask|ConvertTo-Json -Depth 64|ConvertFrom-Json
    $badTask.state='NOT_A_STATE'
    $badTaskRejected=$false
    try{Assert-TaskRecord $badTask}catch{$badTaskRejected=$true}
    Assert-True $badTaskRejected 'task-invalid-state-rejected'

    $request=[pscustomobject]@{protocol_version='scorp.orchestrator/continuation-request-v1';request_id='r1';task_id='task-a';project_id='p';registry_sequence=1;continuation_generation=0;question='choose next exact action';safety_class='standard';previous_action_id='a1';previous_result_sha256=('a'*64);created_at='2026-09-10T00:00:00Z';expires_at='2026-09-11T00:00:00Z'}
    Assert-ContinuationRequest $request
    Write-Output 'PASS continuation-request-runtime-validation'

    $decision=[pscustomobject]@{protocol_version='scorp.orchestrator/continuation-decision-v1';decision_id='d1';request_id='r1';task_id='task-a';expected_registry_sequence=1;previous_action_id='a1';previous_result_sha256=('a'*64);previous_continuation_generation=0;created_at='2026-09-10T00:10:00Z';expires_at='2026-09-11T00:00:00Z';mutation=[pscustomobject]@{kind='SET_NEXT_ACTION';next_action=[pscustomobject]@{protocol_version='scorp.exec/v4';action_id='a2';action_kind='health'}}}
    Assert-ContinuationDecision $decision
    Write-Output 'PASS continuation-decision-runtime-validation'
}finally{
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output 'ORCHESTRATOR_V1_CONTRACT_STORE_PASS'
