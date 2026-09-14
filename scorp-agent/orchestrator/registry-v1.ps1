Set-StrictMode -Version 2.0

function Set-RegistryTaskProperty {
    param($Object,[string]$Name,$Value)
    if(Test-HasProperty $Object $Name){$Object.$Name=$Value}
    else{$Object|Add-Member -NotePropertyName $Name -NotePropertyValue $Value}
}

function Get-RunnableTasks {
    param(
        [Parameter(Mandatory=$true)]$Registry,
        [Parameter(Mandatory=$true)][DateTimeOffset]$Now
    )
    Assert-RegistrySnapshot $Registry
    $runningScopes=@{}
    foreach($task in @($Registry.tasks)){
        if([string]$task.state-cne'RUNNING'){continue}
        foreach($scope in @($task.resource_scope)){
            if(-not[string]::IsNullOrWhiteSpace([string]$scope)){$runningScopes[[string]$scope]=$true}
        }
    }
    $eligible=@()
    foreach($task in @($Registry.tasks)){
        Assert-TaskRecord $task|Out-Null
        if([string]$task.state-cne'READY'){continue}
        if($null-eq$task.next_action){continue}
        if((Test-HasProperty $task 'next_eligible_at') -and $null-ne$task.next_eligible_at -and -not[string]::IsNullOrWhiteSpace([string]$task.next_eligible_at)){
            $next=[DateTimeOffset]::Parse([string]$task.next_eligible_at)
            if($next-gt$Now){continue}
        }
        $depsOk=$true
        foreach($depId in @($task.dependencies)){
            $matches=@($Registry.tasks|Where-Object{[string]$_.task_id-ceq[string]$depId})
            if($matches.Count-ne1 -or [string]$matches[0].state-cne'DONE'){$depsOk=$false;break}
        }
        if(-not$depsOk){continue}
        $conflict=$false
        foreach($scope in @($task.resource_scope)){
            if($runningScopes.ContainsKey([string]$scope)){$conflict=$true;break}
        }
        if($conflict){continue}
        $eligible+=,$task
    }
    return $eligible
}

function Select-NextTask {
    param(
        [Parameter(Mandatory=$true)]$Registry,
        [Parameter(Mandatory=$true)][DateTimeOffset]$Now
    )
    $eligible=@(Get-RunnableTasks -Registry $Registry -Now $Now)
    if($eligible.Count-eq0){return $null}
    return @($eligible|Sort-Object `
        @{Expression={[int]$_.priority};Descending=$true},`
        @{Expression={if($_.next_eligible_at){[DateTimeOffset]::Parse([string]$_.next_eligible_at)}else{[DateTimeOffset]::MinValue}};Ascending=$true},`
        @{Expression={[string]$_.task_id};Ascending=$true})[0]
}

function Invoke-RegistryMutation {
    param(
        [Parameter(Mandatory=$true)][string]$Root,
        [Parameter(Mandatory=$true)][string]$TaskId,
        [Parameter(Mandatory=$true)]$Mutation
    )
    Assert-RequiredProperties $Mutation @('kind') 'MUTATION'
    $registry=Recover-RegistryFromWal -Root $Root
    $matches=@($registry.tasks|Where-Object{[string]$_.task_id-ceq$TaskId})
    if($matches.Count-ne1){throw "TASK_LOOKUP_AMBIGUOUS task_id=$TaskId count=$($matches.Count)"}
    $task=$matches[0]
    Assert-TaskRecord $task|Out-Null
    if(Test-HasProperty $Mutation 'expected_generation'){
        if([int64]$Mutation.expected_generation-ne[int64]$task.generation){throw "TASK_GENERATION_MISMATCH expected=$($Mutation.expected_generation) actual=$($task.generation)"}
    }
    $newTask=$task|ConvertTo-Json -Depth 64|ConvertFrom-Json
    $incrementGeneration=$true
    switch([string]$Mutation.kind){
        'SET_WAITING' {
            $newTask.state='WAITING'
            $newTask.waiting_reason=if((Test-HasProperty $Mutation 'waiting_reason') -and $null-ne$Mutation.waiting_reason){[string]$Mutation.waiting_reason}else{'GPT_CONTINUATION_REQUIRED'}
            $newTask.lease=$null
            if(Test-HasProperty $Mutation 'clear_next_action' -and [bool]$Mutation.clear_next_action){$newTask.next_action=$null}
        }
        'SET_RUNNING_LEASE' {
            if([string]$task.state-cne'READY'){throw "RUNNING_LEASE_REQUIRES_READY actual=$($task.state)"}
            if(-not(Test-HasProperty $Mutation 'lease') -or $null-eq$Mutation.lease){throw 'RUNNING_LEASE_MISSING'}
            $newTask.state='RUNNING'
            $newTask.lease=$Mutation.lease
            $newTask.waiting_reason=$null
            $newTask.blocker=$null
            $newTask.attempts=[int64]$task.attempts+1
            $incrementGeneration=$false
        }
        'UPDATE_LEASE' {
            if([string]$task.state-cne'RUNNING'){throw "UPDATE_LEASE_REQUIRES_RUNNING actual=$($task.state)"}
            if(-not(Test-HasProperty $Mutation 'lease') -or $null-eq$Mutation.lease){throw 'UPDATE_LEASE_MISSING'}
            $newTask.lease=$Mutation.lease
            $incrementGeneration=$false
        }
        'SET_CONTINUATION_REQUEST' {
            if([string]$task.state-cne'WAITING'){throw "CONT_REQUEST_REQUIRES_WAITING actual=$($task.state)"}
            if(-not(Test-HasProperty $Mutation 'request_id') -or [string]::IsNullOrWhiteSpace([string]$Mutation.request_id)){throw 'CONT_REQUEST_ID_MISSING'}
            $newTask.continuation_request_id=[string]$Mutation.request_id
            $newTask.waiting_reason='GPT_CONTINUATION_REQUIRED'
            $incrementGeneration=$false
        }
        'SET_BLOCKED' {
            $newTask.state='BLOCKED'
            $newTask.next_action=$null
            $newTask.lease=$null
            $newTask.waiting_reason=$null
            $reason=if((Test-HasProperty $Mutation 'reason') -and $null-ne$Mutation.reason){$Mutation.reason}else{'ORCHESTRATOR_BLOCKED'}
            Set-RegistryTaskProperty -Object $newTask -Name 'blocker' -Value $reason
        }
        'APPLY_V4_RESULT' {
            if([string]$task.state-cne'RUNNING'){throw "APPLY_RESULT_REQUIRES_RUNNING actual=$($task.state)"}
            if(-not(Test-HasProperty $Mutation 'result') -or $null-eq$Mutation.result){throw 'V4_RESULT_MISSING'}
            $result=$Mutation.result
            $status=[string]$result.status
            $newTask.last_result=$result
            $newTask.lease=$null
            $newTask.continuation_request_id=$null
            $newTask.blocker=$null
            switch($status){
                'SUCCEEDED' {
                    if((Test-HasProperty $Mutation 'terminal_on_success') -and [bool]$Mutation.terminal_on_success){
                        $newTask.state='DONE'
                        $newTask.next_action=$null
                        $newTask.waiting_reason=$null
                    }elseif((Test-HasProperty $Mutation 'next_action') -and $null-ne$Mutation.next_action){
                        $newTask.state='READY'
                        $newTask.next_action=$Mutation.next_action
                        $newTask.waiting_reason=$null
                        $newTask.checkpoint=$null
                    }else{
                        $newTask.state='WAITING'
                        $newTask.next_action=$null
                        $newTask.waiting_reason='GPT_CONTINUATION_REQUIRED'
                    }
                }
                'BLOCKED' {
                    $newTask.state='BLOCKED'
                    $newTask.next_action=$null
                    $newTask.waiting_reason=$null
                    $newTask.blocker=[pscustomobject]@{kind='V4_BLOCKED';result_sha256=[string]$result.result_sha256}
                }
                'FAILED' {
                    $newTask.state='WAITING'
                    $newTask.next_action=$null
                    $newTask.waiting_reason='GPT_CONTINUATION_REQUIRED'
                }
                'TIMED_OUT' {
                    $newTask.state='WAITING'
                    $newTask.next_action=$null
                    $newTask.waiting_reason='GPT_CONTINUATION_REQUIRED'
                }
                'PRECONDITION_FAILED' {
                    $newTask.state='WAITING'
                    $newTask.next_action=$null
                    $newTask.waiting_reason='GPT_CONTINUATION_REQUIRED'
                }
                default {
                    $newTask.state='BLOCKED'
                    $newTask.next_action=$null
                    $newTask.waiting_reason=$null
                    $newTask.blocker=[pscustomobject]@{kind='UNKNOWN_V4_TERMINAL_STATUS';status=$status;result_sha256=[string]$result.result_sha256}
                }
            }
        }
        default {throw "MUTATION_KIND_UNSUPPORTED kind=$($Mutation.kind)"}
    }
    if($incrementGeneration){$newTask.generation=[int64]$task.generation+1}else{$newTask.generation=[int64]$task.generation}
    $stamp=if((Test-HasProperty $Mutation 'updated_at') -and -not[string]::IsNullOrWhiteSpace([string]$Mutation.updated_at)){[string]$Mutation.updated_at}else{[DateTimeOffset]::UtcNow.ToString('o')}
    $newTask.updated_at=$stamp
    Assert-TaskRecord $newTask|Out-Null

    $newTasks=@()
    foreach($candidate in @($registry.tasks)){
        if([string]$candidate.task_id-ceq$TaskId){$newTasks+=,$newTask}else{$newTasks+=,$candidate}
    }
    $prior=[int64]$registry.sequence
    $next=$prior+1
    $newRegistry=[ordered]@{protocol_version='scorp.orchestrator/v1';sequence=$next;tasks=$newTasks;updated_at=$stamp}
    $wal=[ordered]@{protocol_version='scorp.orchestrator/wal-v1';sequence=$next;prior_sequence=$prior;mutation=[string]$Mutation.kind;task_id=$TaskId;registry=$newRegistry}
    Append-WalRecord -Root $Root -Record $wal
    Write-RegistrySnapshot -Root $Root -Registry $newRegistry
    return $newTask
}
