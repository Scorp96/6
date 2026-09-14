Set-StrictMode -Version 2.0

function Set-ContinuationProperty {
    param($Object,[string]$Name,$Value)
    if($null-eq$Object){throw "CONTINUATION_OBJECT_NULL name=$Name"}
    if(Test-HasProperty $Object $Name){$Object.$Name=$Value}
    else{$Object|Add-Member -NotePropertyName $Name -NotePropertyValue $Value}
}

function Get-ContinuationOutboxPath {
    param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)][string]$RequestId)
    $dir=Join-Path $Root 'continuation-outbox'
    if(-not(Test-Path -LiteralPath $dir -PathType Container)){New-Item -ItemType Directory -Path $dir -Force|Out-Null}
    return (Join-Path $dir ($RequestId+'.json'))
}

function New-ContinuationRequest {
    param(
        [Parameter(Mandatory=$true)]$Task,
        [Parameter(Mandatory=$true)]$Result,
        [Parameter(Mandatory=$true)][string]$Question,
        [Parameter(Mandatory=$true)][int64]$RegistrySequence
    )
    if([string]::IsNullOrWhiteSpace($Question)){throw 'CONTINUATION_QUESTION_EMPTY'}
    $created=[DateTimeOffset]::UtcNow
    $request=[pscustomobject][ordered]@{
        protocol_version='scorp.orchestrator/continuation-request-v1'
        request_id=('cont-'+[guid]::NewGuid().ToString('N'))
        task_id=[string]$Task.task_id
        project_id=[string]$Task.project_id
        registry_sequence=$RegistrySequence
        continuation_generation=[int64]$Task.generation
        question=$Question
        safety_class=[string]$Task.safety_class
        previous_action_id=if($null-ne$Result-and(Test-HasProperty $Result 'action_id')){[string]$Result.action_id}else{$null}
        previous_result_sha256=if($null-ne$Result-and(Test-HasProperty $Result 'result_sha256')){[string]$Result.result_sha256}else{$null}
        created_at=$created.ToString('o')
        expires_at=$created.AddHours(24).ToString('o')
        issue_number=$null
        publication_state='NEW'
    }
    [void](Assert-ContinuationRequest $request)
    return $request
}

function Get-ContinuationBindingText {
    param([Parameter(Mandatory=$true)]$Request)
    $copy=[ordered]@{}
    foreach($name in @('protocol_version','request_id','task_id','project_id','registry_sequence','continuation_generation','question','safety_class','previous_action_id','previous_result_sha256','created_at','expires_at')){
        if(Test-HasProperty $Request $name){$copy[$name]=$Request.$name}
    }
    return "SCORP_CONT_REQ`n"+($copy|ConvertTo-Json -Depth 64 -Compress)
}

function Find-ContinuationIssueByIdentity {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Request)
    $matches=@($Context.GitHub.SearchIssues.Invoke([string]$Request.request_id))
    $needle='"request_id":"'+[string]$Request.request_id+'"'
    $exact=@($matches|Where-Object{([string]$_.body).Contains('SCORP_CONT_REQ') -and ([string]$_.body).Contains($needle)})
    if($exact.Count-eq0){return $null}
    if($exact.Count-gt1){throw "DUPLICATE_CONTINUATION_ISSUE request_id=$($Request.request_id) count=$($exact.Count)"}
    return $exact[0]
}

function Publish-ContinuationRequest {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Request)
    [void](Assert-ContinuationRequest $Request)
    Assert-RequiredProperties $Context @('Root','GitHub') 'CONT_CONTEXT'
    $path=Get-ContinuationOutboxPath -Root ([string]$Context.Root) -RequestId ([string]$Request.request_id)

    if(Test-Path -LiteralPath $path -PathType Leaf){
        try{$existing=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json}catch{throw "CONTINUATION_OUTBOX_INVALID_JSON request_id=$($Request.request_id)"}
        if([string]$existing.request_id-cne[string]$Request.request_id){throw 'CONTINUATION_OUTBOX_IDENTITY_MISMATCH'}
        if((Test-HasProperty $existing 'issue_number') -and $null-ne$existing.issue_number -and [int]$existing.issue_number-gt0){return $existing}
    }else{
        Write-Utf8NoBomAtomic -Path $path -Text ($Request|ConvertTo-Json -Depth 64)
    }

    if((Test-HasProperty $Request 'issue_number') -and $null-ne$Request.issue_number -and [int]$Request.issue_number-gt0){
        Write-Utf8NoBomAtomic -Path $path -Text ($Request|ConvertTo-Json -Depth 64)
        return $Request
    }

    $title='[SCORP_CONT_REQ] '+[string]$Request.task_id+' '+[string]$Request.request_id
    $body=Get-ContinuationBindingText -Request $Request
    try{
        $issue=$Context.GitHub.CreateIssue.Invoke($title,$body)
        if($null-eq$issue-or[int]$issue.number-le0){throw 'CONTINUATION_CREATE_INVALID_RESPONSE'}
        Set-ContinuationProperty -Object $Request -Name 'issue_number' -Value ([int]$issue.number)
        Set-ContinuationProperty -Object $Request -Name 'publication_state' -Value 'PUBLISHED'
    }catch{
        $found=Find-ContinuationIssueByIdentity -Context $Context -Request $Request
        if($null-eq$found){throw "AMBIGUOUS_CONTINUATION_PUBLICATION request_id=$($Request.request_id)"}
        Set-ContinuationProperty -Object $Request -Name 'issue_number' -Value ([int]$found.number)
        Set-ContinuationProperty -Object $Request -Name 'publication_state' -Value 'RECONCILED'
    }
    Write-Utf8NoBomAtomic -Path $path -Text ($Request|ConvertTo-Json -Depth 64)
    return $Request
}

function Read-ContinuationDecision {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Request)
    [void](Assert-ContinuationRequest $Request)
    Assert-RequiredProperties $Context @('GitHub','TrustedActor') 'CONT_CONTEXT'
    if($null-eq$Request.issue_number-or[int]$Request.issue_number-le0){throw 'CONTINUATION_REQUEST_NOT_PUBLISHED'}
    $comments=@($Context.GitHub.ReadComments.Invoke([int]$Request.issue_number))
    $valid=@()
    foreach($comment in $comments){
        $author=$null
        if(Test-HasProperty $comment 'author'){$author=[string]$comment.author}
        elseif((Test-HasProperty $comment 'user') -and $null-ne$comment.user -and (Test-HasProperty $comment.user 'login')){$author=[string]$comment.user.login}
        if($author-cne[string]$Context.TrustedActor){continue}
        $body=[string]$comment.body
        if(-not$body.StartsWith('SCORP_CONT_DECISION')){continue}
        $nl=$body.IndexOf("`n")
        if($nl-lt0){throw 'CONTINUATION_DECISION_PAYLOAD_MISSING'}
        try{$decision=$body.Substring($nl+1).Trim()|ConvertFrom-Json}catch{throw "CONTINUATION_DECISION_INVALID_JSON $($_.Exception.Message)"}
        [void](Assert-ContinuationDecision $decision)
        if([string]$decision.request_id-cne[string]$Request.request_id -or [string]$decision.task_id-cne[string]$Request.task_id){throw 'CONTINUATION_DECISION_IDENTITY_MISMATCH'}
        if([int64]$decision.expected_registry_sequence-ne[int64]$Request.registry_sequence){throw 'CONTINUATION_DECISION_SEQUENCE_MISMATCH'}
        if([int64]$decision.previous_continuation_generation-ne[int64]$Request.continuation_generation){throw 'CONTINUATION_DECISION_GENERATION_MISMATCH'}
        if([string]$decision.previous_action_id-cne[string]$Request.previous_action_id){throw 'CONTINUATION_DECISION_PREVIOUS_ACTION_MISMATCH'}
        if([string]$decision.previous_result_sha256-cne[string]$Request.previous_result_sha256){throw 'CONTINUATION_DECISION_PREVIOUS_RESULT_MISMATCH'}
        if((Test-HasProperty $decision 'expires_at') -and -not[string]::IsNullOrWhiteSpace([string]$decision.expires_at)){
            if([DateTimeOffset]::UtcNow-gt[DateTimeOffset]::Parse([string]$decision.expires_at)){throw 'CONTINUATION_DECISION_EXPIRED'}
        }
        $valid+=,$decision
    }
    if($valid.Count-eq0){return [pscustomobject]@{status='WAITING';decision=$null}}
    if($valid.Count-gt1){throw "DUPLICATE_CONTINUATION_DECISION request_id=$($Request.request_id) count=$($valid.Count)"}
    return [pscustomobject]@{status='DECISION';decision=$valid[0]}
}

function Apply-ContinuationDecision {
    param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)]$Task,[Parameter(Mandatory=$true)]$Decision)
    [void](Assert-ContinuationDecision $Decision)
    $registry=Read-RegistrySnapshot -Root $Root
    $matches=@($registry.tasks|Where-Object{[string]$_.task_id-ceq[string]$Decision.task_id})
    if($matches.Count-ne1){throw "CONTINUATION_TASK_LOOKUP_FAILED task_id=$($Decision.task_id) count=$($matches.Count)"}
    $current=$matches[0]
    if((Test-HasProperty $current 'last_continuation_decision_id') -and [string]$current.last_continuation_decision_id-ceq[string]$Decision.decision_id){
        return [pscustomobject]@{status='ALREADY_APPLIED';task=$current;registry_sequence=[int64]$registry.sequence}
    }
    if([string]$current.task_id-cne[string]$Task.task_id -or [string]$Decision.task_id-cne[string]$Task.task_id){throw 'CONTINUATION_DECISION_TASK_MISMATCH'}
    if([int64]$registry.sequence-ne[int64]$Decision.expected_registry_sequence){throw "STALE_CONTINUATION_DECISION expected=$($Decision.expected_registry_sequence) actual=$($registry.sequence)"}
    if([int64]$current.generation-ne[int64]$Decision.previous_continuation_generation){throw "STALE_CONTINUATION_DECISION generation_expected=$($Decision.previous_continuation_generation) actual=$($current.generation)"}
    if((Test-HasProperty $current 'continuation_request_id') -and $null-ne$current.continuation_request_id -and [string]$current.continuation_request_id-cne[string]$Decision.request_id){throw 'CONTINUATION_REQUEST_TASK_MISMATCH'}
    if($null-ne$current.last_result){
        if((Test-HasProperty $current.last_result 'action_id') -and [string]$current.last_result.action_id-cne[string]$Decision.previous_action_id){throw 'CONTINUATION_PREVIOUS_ACTION_MISMATCH'}
        if((Test-HasProperty $current.last_result 'result_sha256') -and [string]$current.last_result.result_sha256-cne[string]$Decision.previous_result_sha256){throw 'CONTINUATION_PREVIOUS_RESULT_MISMATCH'}
    }

    $inboxDir=Join-Path $Root 'continuation-inbox'
    if(-not(Test-Path -LiteralPath $inboxDir -PathType Container)){New-Item -ItemType Directory -Path $inboxDir -Force|Out-Null}
    $inboxPath=Join-Path $inboxDir ([string]$Decision.request_id+'.json')
    $decisionText=$Decision|ConvertTo-Json -Depth 64
    if(Test-Path -LiteralPath $inboxPath -PathType Leaf){
        try{$existingInbox=Get-Content -LiteralPath $inboxPath -Raw -Encoding UTF8|ConvertFrom-Json}catch{throw 'CONTINUATION_INBOX_INVALID_JSON'}
        $existingCanonical=$existingInbox|ConvertTo-Json -Depth 64 -Compress
        $decisionCanonical=$Decision|ConvertTo-Json -Depth 64 -Compress
        if((Get-Sha256Text $existingCanonical)-cne(Get-Sha256Text $decisionCanonical)){throw 'CONTINUATION_INBOX_DECISION_MISMATCH'}
    }else{
        Write-Utf8NoBomAtomic -Path $inboxPath -Text $decisionText
    }

    $newRegistry=$registry|ConvertTo-Json -Depth 64|ConvertFrom-Json
    $newTask=@($newRegistry.tasks|Where-Object{[string]$_.task_id-ceq[string]$Decision.task_id})[0]
    $kind=[string]$Decision.mutation.kind
    switch($kind){
        'SET_NEXT_ACTION' {
            if($null-eq$Decision.mutation.next_action){throw 'CONTINUATION_NEXT_ACTION_NULL'}
            Set-ContinuationProperty -Object $newTask -Name 'next_action' -Value $Decision.mutation.next_action
            Set-ContinuationProperty -Object $newTask -Name 'state' -Value 'READY'
            Set-ContinuationProperty -Object $newTask -Name 'waiting_reason' -Value $null
            Set-ContinuationProperty -Object $newTask -Name 'blocker' -Value $null
        }
        'SET_TERMINAL' {
            $target=if((Test-HasProperty $Decision.mutation 'state') -and -not[string]::IsNullOrWhiteSpace([string]$Decision.mutation.state)){[string]$Decision.mutation.state}else{'DONE'}
            if(@('DONE','FAILED')-cnotcontains$target){throw "CONTINUATION_TERMINAL_STATE_INVALID state=$target"}
            Set-ContinuationProperty -Object $newTask -Name 'state' -Value $target
            Set-ContinuationProperty -Object $newTask -Name 'next_action' -Value $null
        }
        'SET_WAITING' {
            Set-ContinuationProperty -Object $newTask -Name 'state' -Value 'WAITING'
            Set-ContinuationProperty -Object $newTask -Name 'waiting_reason' -Value ([string]$Decision.mutation.reason)
            Set-ContinuationProperty -Object $newTask -Name 'next_action' -Value $null
        }
        'SET_BLOCKED' {
            Set-ContinuationProperty -Object $newTask -Name 'state' -Value 'BLOCKED'
            Set-ContinuationProperty -Object $newTask -Name 'blocker' -Value ([string]$Decision.mutation.reason)
            Set-ContinuationProperty -Object $newTask -Name 'next_action' -Value $null
        }
        default {throw "CONTINUATION_MUTATION_NOT_IMPLEMENTED kind=$kind"}
    }
    Set-ContinuationProperty -Object $newTask -Name 'continuation_request_id' -Value $null
    Set-ContinuationProperty -Object $newTask -Name 'last_continuation_decision_id' -Value ([string]$Decision.decision_id)
    Set-ContinuationProperty -Object $newTask -Name 'last_continuation_request_id' -Value ([string]$Decision.request_id)
    Set-ContinuationProperty -Object $newTask -Name 'generation' -Value ([int64]$newTask.generation+1)
    Set-ContinuationProperty -Object $newTask -Name 'updated_at' -Value ([DateTimeOffset]::UtcNow.ToString('o'))
    $prior=[int64]$newRegistry.sequence
    $newRegistry.sequence=$prior+1
    if(Test-HasProperty $newRegistry 'updated_at'){$newRegistry.updated_at=[DateTimeOffset]::UtcNow.ToString('o')}
    else{$newRegistry|Add-Member -NotePropertyName updated_at -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString('o'))}
    $wal=[ordered]@{protocol_version='scorp.orchestrator/wal-v1';sequence=[int64]$newRegistry.sequence;prior_sequence=$prior;mutation='CONTINUATION_DECISION';registry=$newRegistry}
    Append-WalRecord -Root $Root -Record $wal
    Write-RegistrySnapshot -Root $Root -Registry $newRegistry
    return [pscustomobject]@{status='APPLIED';task=$newTask;registry_sequence=[int64]$newRegistry.sequence}
}

function Finalize-ContinuationRequest {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Request,[Parameter(Mandatory=$true)]$Decision)
    Assert-RequiredProperties $Context @('GitHub') 'CONT_CONTEXT'
    if($null-eq$Request.issue_number-or[int]$Request.issue_number-le0){throw 'CONTINUATION_REQUEST_NOT_PUBLISHED'}
    $title='[SCORP_CONT_DONE] '+[string]$Request.task_id+' '+[string]$Request.request_id
    try{
        [void]$Context.GitHub.UpdateIssue.Invoke([int]$Request.issue_number,$title,'closed')
        return [pscustomobject]@{status='FINALIZED';issue_number=[int]$Request.issue_number;decision_id=[string]$Decision.decision_id}
    }catch{
        return [pscustomobject]@{status='CLEANUP_PENDING';issue_number=[int]$Request.issue_number;decision_id=[string]$Decision.decision_id;error=$_.Exception.Message}
    }
}
