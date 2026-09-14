Set-StrictMode -Version 2.0

function Test-HasProperty {
    param($Object,[string]$Name)
    if($null-eq$Object){return $false}
    if($Object -is [System.Collections.IDictionary]){return $Object.Contains($Name)}
    return @($Object.PSObject.Properties.Name) -contains $Name
}

function Assert-RequiredProperties {
    param($Object,[string[]]$Names,[string]$Prefix)
    foreach($name in $Names){
        if(-not(Test-HasProperty $Object $name)){throw "${Prefix}_MISSING_PROPERTY name=$name"}
    }
}

function Write-Utf8NoBomAtomic {
    param([Parameter(Mandatory=$true)][string]$Path,[Parameter(Mandatory=$true)][AllowEmptyString()][string]$Text)
    $dir=Split-Path -Parent $Path
    if([string]::IsNullOrWhiteSpace($dir)){throw 'ATOMIC_WRITE_PARENT_REQUIRED'}
    if(-not(Test-Path -LiteralPath $dir -PathType Container)){New-Item -ItemType Directory -Path $dir -Force|Out-Null}
    $id=[guid]::NewGuid().ToString('N')
    $tmp=Join-Path $dir ('.{0}.{1}.tmp' -f [IO.Path]::GetFileName($Path),$id)
    $bak=Join-Path $dir ('.{0}.{1}.bak' -f [IO.Path]::GetFileName($Path),$id)
    try{
        $encoding=New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($tmp,$Text,$encoding)
        if(Test-Path -LiteralPath $Path -PathType Leaf){
            [IO.File]::Replace($tmp,$Path,$bak,$true)
        }else{
            [IO.File]::Move($tmp,$Path)
        }
    }finally{
        Remove-Item -LiteralPath $tmp,$bak -Force -ErrorAction SilentlyContinue
    }
}

function Get-Sha256Text {
    param([Parameter(Mandatory=$true)][AllowEmptyString()][string]$Text)
    $encoding=New-Object Text.UTF8Encoding($false)
    $bytes=$encoding.GetBytes($Text)
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$hash=$sha.ComputeHash($bytes)}finally{$sha.Dispose()}
    return (($hash|ForEach-Object{$_.ToString('x2')}) -join '')
}

function Append-WalRecord {
    param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)]$Record)
    if(-not(Test-Path -LiteralPath $Root -PathType Container)){New-Item -ItemType Directory -Path $Root -Force|Out-Null}
    $path=Join-Path $Root 'journal.jsonl'
    $line=($Record|ConvertTo-Json -Depth 64 -Compress)+[Environment]::NewLine
    $encoding=New-Object Text.UTF8Encoding($false)
    $bytes=$encoding.GetBytes($line)
    $fs=New-Object IO.FileStream($path,[IO.FileMode]::Append,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    try{
        $fs.Write($bytes,0,$bytes.Length)
        $fs.Flush($true)
    }finally{
        $fs.Dispose()
    }
}

function Assert-RegistrySnapshot {
    param($Registry)
    Assert-RequiredProperties $Registry @('protocol_version','sequence','tasks') 'REGISTRY'
    if([string]$Registry.protocol_version-cne'scorp.orchestrator/v1'){throw "REGISTRY_PROTOCOL_MISMATCH actual=$($Registry.protocol_version)"}
    if([int64]$Registry.sequence-lt0){throw "REGISTRY_SEQUENCE_INVALID actual=$($Registry.sequence)"}
    if($null-eq$Registry.tasks){throw 'REGISTRY_TASKS_NULL'}
}

function Write-RegistrySnapshot {
    param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)]$Registry)
    Assert-RegistrySnapshot $Registry
    if(-not(Test-Path -LiteralPath $Root -PathType Container)){New-Item -ItemType Directory -Path $Root -Force|Out-Null}
    $text=$Registry|ConvertTo-Json -Depth 64
    Write-Utf8NoBomAtomic -Path (Join-Path $Root 'registry.json') -Text $text
}

function Read-RegistrySnapshot {
    param([Parameter(Mandatory=$true)][string]$Root)
    $path=Join-Path $Root 'registry.json'
    if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "REGISTRY_SNAPSHOT_MISSING path=$path"}
    try{$registry=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json}catch{throw "REGISTRY_SNAPSHOT_INVALID_JSON $($_.Exception.Message)"}
    Assert-RegistrySnapshot $registry
    return $registry
}

function Recover-RegistryFromWal {
    param([Parameter(Mandatory=$true)][string]$Root)
    $registry=Read-RegistrySnapshot -Root $Root
    $current=[int64]$registry.sequence
    $walPath=Join-Path $Root 'journal.jsonl'
    if(-not(Test-Path -LiteralPath $walPath -PathType Leaf)){return $registry}
    $lines=@(Get-Content -LiteralPath $walPath -ErrorAction Stop)
    foreach($line in $lines){
        if([string]::IsNullOrWhiteSpace([string]$line)){continue}
        try{$record=[string]$line|ConvertFrom-Json}catch{throw "WAL_INVALID_JSON $($_.Exception.Message)"}
        Assert-RequiredProperties $record @('protocol_version','sequence','prior_sequence','registry') 'WAL'
        if([string]$record.protocol_version-cne'scorp.orchestrator/wal-v1'){throw "WAL_PROTOCOL_MISMATCH actual=$($record.protocol_version)"}
        $seq=[int64]$record.sequence
        $prior=[int64]$record.prior_sequence
        if($seq-le$current){continue}
        $expected=$current+1
        if($seq-ne$expected){throw "WAL_SEQUENCE_MISMATCH expected=$expected actual=$seq"}
        if($prior-ne$current){throw "WAL_PRIOR_MISMATCH expected=$current actual=$prior"}
        Assert-RegistrySnapshot $record.registry
        if([int64]$record.registry.sequence-ne$seq){throw "WAL_REGISTRY_SEQUENCE_MISMATCH record=$seq registry=$($record.registry.sequence)"}
        $registry=$record.registry
        $current=$seq
    }
    return $registry
}

function Assert-TaskRecord {
    param([Parameter(Mandatory=$true)]$Task)
    $required=@('protocol_version','task_id','project_id','priority','state','dependencies','dependency_policy','parallel_safety','execution_adapter','next_action','attempts','max_attempts','created_at','updated_at','evidence_refs','safety_class','generation','resource_scope')
    Assert-RequiredProperties $Task $required 'TASK'
    if([string]$Task.protocol_version-cne'scorp.orchestrator/task-v1'){throw "TASK_PROTOCOL_MISMATCH actual=$($Task.protocol_version)"}
    if([string]::IsNullOrWhiteSpace([string]$Task.task_id)){throw 'TASK_ID_EMPTY'}
    if([string]::IsNullOrWhiteSpace([string]$Task.project_id)){throw 'TASK_PROJECT_EMPTY'}
    $states=@('STAGED','READY','RUNNING','DONE','FAILED','WAITING','BLOCKED')
    if($states-cnotcontains[string]$Task.state){throw "TASK_STATE_INVALID actual=$($Task.state)"}
    if([string]$Task.dependency_policy-cne'all_done'){throw "TASK_DEPENDENCY_POLICY_INVALID actual=$($Task.dependency_policy)"}
    $adapters=@('LOCAL_V4','GPT_HOST')
    if($adapters-cnotcontains[string]$Task.execution_adapter){throw "TASK_EXECUTION_ADAPTER_INVALID actual=$($Task.execution_adapter)"}
    if([int64]$Task.attempts-lt0){throw 'TASK_ATTEMPTS_INVALID'}
    if([int64]$Task.max_attempts-lt1){throw 'TASK_MAX_ATTEMPTS_INVALID'}
    if([int64]$Task.generation-lt0){throw 'TASK_GENERATION_INVALID'}
    if($null-eq$Task.dependencies){throw 'TASK_DEPENDENCIES_NULL'}
    if($null-eq$Task.evidence_refs){throw 'TASK_EVIDENCE_REFS_NULL'}
    if($null-eq$Task.resource_scope){throw 'TASK_RESOURCE_SCOPE_NULL'}
    return $true
}

function Assert-ContinuationRequest {
    param([Parameter(Mandatory=$true)]$Request)
    $required=@('protocol_version','request_id','task_id','project_id','registry_sequence','continuation_generation','question','safety_class','created_at','expires_at')
    Assert-RequiredProperties $Request $required 'CONT_REQUEST'
    if([string]$Request.protocol_version-cne'scorp.orchestrator/continuation-request-v1'){throw "CONT_REQUEST_PROTOCOL_MISMATCH actual=$($Request.protocol_version)"}
    foreach($name in @('request_id','task_id','project_id','question','safety_class','created_at','expires_at')){
        if([string]::IsNullOrWhiteSpace([string]$Request.$name)){throw "CONT_REQUEST_EMPTY_PROPERTY name=$name"}
    }
    if([int64]$Request.registry_sequence-lt0){throw 'CONT_REQUEST_REGISTRY_SEQUENCE_INVALID'}
    if([int64]$Request.continuation_generation-lt0){throw 'CONT_REQUEST_GENERATION_INVALID'}
    if((Test-HasProperty $Request 'previous_result_sha256') -and $null-ne$Request.previous_result_sha256 -and [string]$Request.previous_result_sha256-cnotmatch'^[0-9a-f]{64}$'){throw 'CONT_REQUEST_RESULT_HASH_INVALID'}
    return $true
}

function Assert-ContinuationDecision {
    param([Parameter(Mandatory=$true)]$Decision)
    $required=@('protocol_version','decision_id','request_id','task_id','expected_registry_sequence','previous_continuation_generation','mutation')
    Assert-RequiredProperties $Decision $required 'CONT_DECISION'
    if([string]$Decision.protocol_version-cne'scorp.orchestrator/continuation-decision-v1'){throw "CONT_DECISION_PROTOCOL_MISMATCH actual=$($Decision.protocol_version)"}
    foreach($name in @('decision_id','request_id','task_id')){
        if([string]::IsNullOrWhiteSpace([string]$Decision.$name)){throw "CONT_DECISION_EMPTY_PROPERTY name=$name"}
    }
    if([int64]$Decision.expected_registry_sequence-lt0){throw 'CONT_DECISION_REGISTRY_SEQUENCE_INVALID'}
    if([int64]$Decision.previous_continuation_generation-lt0){throw 'CONT_DECISION_GENERATION_INVALID'}
    if($null-eq$Decision.mutation){throw 'CONT_DECISION_MUTATION_NULL'}
    Assert-RequiredProperties $Decision.mutation @('kind') 'CONT_DECISION_MUTATION'
    $kinds=@('SET_NEXT_ACTION','SET_TERMINAL','CREATE_CHILD_TASKS','SET_WAITING','SET_BLOCKED')
    if($kinds-cnotcontains[string]$Decision.mutation.kind){throw "CONT_DECISION_MUTATION_KIND_INVALID actual=$($Decision.mutation.kind)"}
    if((Test-HasProperty $Decision 'previous_result_sha256') -and $null-ne$Decision.previous_result_sha256 -and [string]$Decision.previous_result_sha256-cnotmatch'^[0-9a-f]{64}$'){throw 'CONT_DECISION_RESULT_HASH_INVALID'}
    return $true
}