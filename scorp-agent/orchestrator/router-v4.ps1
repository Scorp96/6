Set-StrictMode -Version 2.0

function Set-OrAddProperty {
    param($Object,[string]$Name,$Value)
    if(Test-HasProperty $Object $Name){$Object.$Name=$Value}
    else{$Object|Add-Member -MemberType NoteProperty -Name $Name -Value $Value}
}

function ConvertTo-V4EnvelopeText {
    param([Parameter(Mandatory=$true)][string]$V4TaskId,[Parameter(Mandatory=$true)]$Action)
    if([string]$Action.protocol_version-cne'scorp.exec/v4'){throw "V4_ACTION_PROTOCOL_MISMATCH actual=$($Action.protocol_version)"}
    if([string]::IsNullOrWhiteSpace([string]$Action.action_id)){throw 'V4_ACTION_ID_EMPTY'}
    $copy=$Action|ConvertTo-Json -Depth 64|ConvertFrom-Json
    Set-OrAddProperty -Object $copy -Name 'task_id' -Value $V4TaskId
    Set-OrAddProperty -Object $copy -Name 'issue_number' -Value $null
    Set-OrAddProperty -Object $copy -Name 'claim_token' -Value $null
    return ($copy|ConvertTo-Json -Depth 64 -Compress)
}

function New-V4Lease {
    param([Parameter(Mandatory=$true)]$Task,[Parameter(Mandatory=$true)]$Action)
    Assert-TaskRecord $Task|Out-Null
    $v4TaskId='orch/{0}/g{1}' -f [string]$Task.task_id,[int64]$Task.generation
    $body=ConvertTo-V4EnvelopeText -V4TaskId $v4TaskId -Action $Action
    $hash=Get-Sha256Text $body
    $leaseSeed='{0}|{1}|{2}|{3}' -f [string]$Task.task_id,[int64]$Task.generation,[string]$Action.action_id,$hash
    return [pscustomobject]@{
        protocol_version='scorp.orchestrator/v4-lease-v1'
        lease_id=(Get-Sha256Text $leaseSeed)
        orchestrator_task_id=[string]$Task.task_id
        orchestrator_generation=[int64]$Task.generation
        v4_task_id=$v4TaskId
        v4_action_id=[string]$Action.action_id
        envelope_hash=$hash
        issue_number=$null
        claim_token=$null
        publication_state='PENDING'
        terminal_applied=$false
        terminal_result_sha256=$null
        terminal_status=$null
    }
}

function Get-V4BindingText {
    param([Parameter(Mandatory=$true)]$Lease,[Parameter(Mandatory=$true)]$Action)
    return ConvertTo-V4EnvelopeText -V4TaskId ([string]$Lease.v4_task_id) -Action $Action
}

function Assert-PersistedV4Lease {
    param($Task,$Lease)
    if($null-eq$Task.lease){throw 'LEASE_NOT_PERSISTED: task.lease is null'}
    foreach($name in @('lease_id','orchestrator_task_id','orchestrator_generation','v4_task_id','v4_action_id','envelope_hash')){
        if(-not(Test-HasProperty $Task.lease $name)){throw "LEASE_NOT_PERSISTED: task lease missing $name"}
    }
    if([string]$Task.lease.lease_id-cne[string]$Lease.lease_id){throw 'LEASE_NOT_PERSISTED: lease_id mismatch'}
    if([string]$Task.lease.orchestrator_task_id-cne[string]$Task.task_id){throw 'LEASE_NOT_PERSISTED: task identity mismatch'}
    if([int64]$Task.lease.orchestrator_generation-ne[int64]$Task.generation){throw 'LEASE_NOT_PERSISTED: generation mismatch'}
    if([string]$Task.lease.envelope_hash-cne[string]$Lease.envelope_hash){throw 'LEASE_NOT_PERSISTED: envelope hash mismatch'}
}

function Get-IssueNumber {
    param($Issue)
    foreach($name in @('number','issue_number')){
        if(Test-HasProperty $Issue $name){return [int]$Issue.$name}
    }
    throw 'V4_ISSUE_NUMBER_MISSING'
}

function Test-V4IssueIdentity {
    param($Issue,$Lease)
    if($null-eq$Issue -or -not(Test-HasProperty $Issue 'body')){return $false}
    try{$env=[string]$Issue.body|ConvertFrom-Json}catch{return $false}
    if([string]$env.protocol_version-cne'scorp.exec/v4'){return $false}
    if([string]$env.task_id-cne[string]$Lease.v4_task_id){return $false}
    if([string]$env.action_id-cne[string]$Lease.v4_action_id){return $false}
    $hash=Get-Sha256Text ([string]$Issue.body)
    return $hash-ceq[string]$Lease.envelope_hash
}

function Find-V4IssueByIdentity {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Lease)
    $identity='{0} {1} {2}' -f [string]$Lease.v4_task_id,[string]$Lease.v4_action_id,[string]$Lease.envelope_hash
    $found=@($Context.GitHub.SearchIssues.Invoke($identity))
    $exact=@()
    foreach($issue in $found){if(Test-V4IssueIdentity -Issue $issue -Lease $Lease){$exact+=,$issue}}
    return $exact
}

function Publish-V4Action {
    param(
        [Parameter(Mandatory=$true)]$Context,
        [Parameter(Mandatory=$true)]$Task,
        [Parameter(Mandatory=$true)]$Lease,
        [Parameter(Mandatory=$true)]$Action
    )
    Assert-PersistedV4Lease -Task $Task -Lease $Lease
    if($null-ne$Lease.issue_number){return $Lease}
    $body=Get-V4BindingText -Lease $Lease -Action $Action
    if((Get-Sha256Text $body)-cne[string]$Lease.envelope_hash){throw 'LEASE_ENVELOPE_HASH_MISMATCH'}
    $title='[SCORP_EXEC] {0} {1}' -f [string]$Lease.v4_task_id,[string]$Lease.v4_action_id
    try{
        $created=@($Context.GitHub.CreateIssue.Invoke($title,$body))
        if($created.Count-ne1){throw "CREATE_ISSUE_RESPONSE_AMBIGUOUS count=$($created.Count)"}
        $Lease.issue_number=Get-IssueNumber $created[0]
        $Lease.publication_state='PUBLISHED'
        return $Lease
    }catch{
        $matches=@(Find-V4IssueByIdentity -Context $Context -Lease $Lease)
        if($matches.Count-eq0){throw "AMBIGUOUS_PUBLICATION: create acknowledgement lost and zero exact matches for lease=$($Lease.lease_id)"}
        if($matches.Count-gt1){throw "DUPLICATE_V4_ISSUE: exact_matches=$($matches.Count) lease=$($Lease.lease_id)"}
        $Lease.issue_number=Get-IssueNumber $matches[0]
        $Lease.publication_state='RECONCILED'
        return $Lease
    }
}

function Get-CommentAuthor {
    param($Comment)
    if(Test-HasProperty $Comment 'author'){return [string]$Comment.author}
    if((Test-HasProperty $Comment 'user') -and $null-ne$Comment.user -and (Test-HasProperty $Comment.user 'login')){return [string]$Comment.user.login}
    return ''
}

function Parse-LifecycleFields {
    param([string]$Body)
    $map=@{}
    foreach($line in @($Body -split "`r?`n")){
        $i=$line.IndexOf(':')
        if($i-le0){continue}
        $key=$line.Substring(0,$i).Trim()
        $value=$line.Substring($i+1).Trim()
        $map[$key]=$value
    }
    return $map
}

function Assert-LifecycleIdentity {
    param($Fields,$Lease,[int]$IssueNumber,[string]$ExpectedClaimToken)
    foreach($key in @('Issue','TaskId','ActionId','ClaimToken')){if(-not$Fields.ContainsKey($key)){throw "LIFECYCLE_IDENTITY_MISMATCH missing=$key"}}
    if([int]$Fields['Issue']-ne$IssueNumber){throw 'LIFECYCLE_IDENTITY_MISMATCH issue'}
    if([string]$Fields['TaskId']-cne[string]$Lease.v4_task_id){throw 'LIFECYCLE_IDENTITY_MISMATCH task'}
    if([string]$Fields['ActionId']-cne[string]$Lease.v4_action_id){throw 'LIFECYCLE_IDENTITY_MISMATCH action'}
    if(-not[string]::IsNullOrWhiteSpace($ExpectedClaimToken) -and [string]$Fields['ClaimToken']-cne$ExpectedClaimToken){throw 'LIFECYCLE_IDENTITY_MISMATCH claim'}
}

function Read-V4Lifecycle {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Lease)
    if($null-eq$Lease.issue_number){throw 'V4_LEASE_ISSUE_UNBOUND'}
    $issueNumber=[int]$Lease.issue_number
    $comments=@($Context.GitHub.ReadComments.Invoke($issueNumber))
    $claims=@();$results=@()
    foreach($comment in $comments){
        if((Get-CommentAuthor $comment)-cne[string]$Context.TrustedActor){continue}
        $body=[string]$comment.body
        if($body.StartsWith('SCORP_EXEC_CLAIMED',[StringComparison]::Ordinal)){$claims+=,[pscustomobject]@{body=$body;fields=(Parse-LifecycleFields $body)}}
        elseif($body.StartsWith('SCORP_EXEC_RESULT',[StringComparison]::Ordinal)){$results+=,[pscustomobject]@{body=$body;fields=(Parse-LifecycleFields $body)}}
    }
    if($claims.Count-gt1){throw "DUPLICATE_V4_CLAIM count=$($claims.Count) issue=$issueNumber"}
    if($results.Count-gt1){throw "DUPLICATE_V4_RESULT count=$($results.Count) issue=$issueNumber"}
    if($claims.Count-eq0){
        if($results.Count-gt0){throw 'LIFECYCLE_IDENTITY_MISMATCH result-without-claim'}
        return [pscustomobject]@{status='PENDING';claim_token=$null;result_sha256=$null;exit_code=$null}
    }
    $claimFields=$claims[0].fields
    Assert-LifecycleIdentity -Fields $claimFields -Lease $Lease -IssueNumber $issueNumber -ExpectedClaimToken ''
    $claimToken=[string]$claimFields['ClaimToken']
    if([string]::IsNullOrWhiteSpace($claimToken)){throw 'LIFECYCLE_IDENTITY_MISMATCH empty-claim'}
    $Lease.claim_token=$claimToken
    if($results.Count-eq0){return [pscustomobject]@{status='RUNNING';claim_token=$claimToken;result_sha256=$null;exit_code=$null}}
    $resultFields=$results[0].fields
    Assert-LifecycleIdentity -Fields $resultFields -Lease $Lease -IssueNumber $issueNumber -ExpectedClaimToken $claimToken
    foreach($key in @('Status','ExitCode','ResultSHA256')){if(-not$resultFields.ContainsKey($key)){throw "LIFECYCLE_RESULT_MISSING field=$key"}}
    $resultHash=[string]$resultFields['ResultSHA256']
    if($resultHash-cnotmatch'^[0-9a-f]{64}$'){throw 'LIFECYCLE_RESULT_HASH_INVALID'}
    return [pscustomobject]@{status=[string]$resultFields['Status'];claim_token=$claimToken;result_sha256=$resultHash;exit_code=[int]$resultFields['ExitCode']}
}

function Reconcile-V4Lease {
    param([Parameter(Mandatory=$true)]$Context,[Parameter(Mandatory=$true)]$Task)
    if($null-eq$Task.lease){throw 'V4_LEASE_MISSING'}
    $lease=$Task.lease
    if([int64]$lease.orchestrator_generation-ne[int64]$Task.generation){throw "LEASE_GENERATION_MISMATCH lease=$($lease.orchestrator_generation) task=$($Task.generation)"}
    if([bool]$lease.terminal_applied){return [pscustomobject]@{status='ALREADY_APPLIED';result_sha256=$lease.terminal_result_sha256}}
    $life=Read-V4Lifecycle -Context $Context -Lease $lease
    if([string]$life.status-ceq'PENDING' -or [string]$life.status-ceq'RUNNING'){return $life}
    $lease.terminal_applied=$true
    $lease.terminal_result_sha256=[string]$life.result_sha256
    $lease.terminal_status=[string]$life.status
    $lease.claim_token=[string]$life.claim_token
    return [pscustomobject]@{status='TERMINAL';terminal_status=[string]$life.status;result_sha256=[string]$life.result_sha256;exit_code=$life.exit_code}
}

function Invoke-OrchestratorGhJson {
    param(
        [Parameter(Mandatory=$true)][string]$Method,
        [Parameter(Mandatory=$true)][string]$Endpoint,
        [hashtable]$Fields=$null,
        [switch]$Paginate,
        [int]$TimeoutSeconds=30
    )
    if($Method-cnotmatch'^[A-Z]+$'){throw "GITHUB_API_METHOD_INVALID method=$Method"}
    if([string]::IsNullOrWhiteSpace($Endpoint)-or$Endpoint-match'[\s\r\n"]'){throw "GITHUB_API_ENDPOINT_INVALID endpoint=$Endpoint"}
    $gh=(Get-Command gh -CommandType Application -ErrorAction Stop).Source
    $args=@('api','--method',$Method)
    if($Paginate){$args+=@('--paginate','--slurp')}
    $args+=$Endpoint
    $inputJson=$null
    if($null-ne$Fields){
        $args+=@('--input','-')
        $inputJson=$Fields|ConvertTo-Json -Depth 32 -Compress
    }
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$gh
    $psi.Arguments=($args -join ' ')
    $psi.UseShellExecute=$false
    $psi.CreateNoWindow=$true
    $psi.RedirectStandardOutput=$true
    $psi.RedirectStandardError=$true
    $psi.RedirectStandardInput=($null-ne$Fields)
    $proc=New-Object System.Diagnostics.Process
    $proc.StartInfo=$psi
    try{
        if(-not$proc.Start()){throw "GITHUB_API_START_FAILED method=$Method endpoint=$Endpoint"}
        $stdoutTask=$proc.StandardOutput.ReadToEndAsync()
        $stderrTask=$proc.StandardError.ReadToEndAsync()
        if($null-ne$Fields){
            $proc.StandardInput.Write($inputJson)
            $proc.StandardInput.Close()
        }
        $waitMs=[Math]::Max(1,$TimeoutSeconds)*1000
        if(-not$proc.WaitForExit($waitMs)){
            try{$proc.Kill()}catch{}
            try{[void]$proc.WaitForExit(2000)}catch{}
            throw "GITHUB_API_TIMEOUT method=$Method endpoint=$Endpoint timeout_seconds=$TimeoutSeconds"
        }
        $stdoutTask.Wait()
        $stderrTask.Wait()
        $text=[string]$stdoutTask.Result
        $err=[string]$stderrTask.Result
        $code=[int]$proc.ExitCode
        if($code-ne0){throw "GITHUB_API_FAILED method=$Method endpoint=$Endpoint exit=$code stderr=$err"}
        $text=$text.Trim()
        if([string]::IsNullOrWhiteSpace($text)){return $null}
        try{return ($text|ConvertFrom-Json)}catch{throw "GITHUB_API_INVALID_JSON method=$Method endpoint=$Endpoint $($_.Exception.Message)"}
    }finally{
        $proc.Dispose()
    }
}

function New-GitHubCliAdapter {
    param([Parameter(Mandatory=$true)][string]$RepoFullName)
    if($RepoFullName-cnotmatch'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'){throw "GITHUB_REPO_INVALID value=$RepoFullName"}
    [void](Get-Command gh -CommandType Application -ErrorAction Stop)
    $create={
        param($title,$body)
        Invoke-OrchestratorGhJson -Method 'POST' -Endpoint ('/repos/'+$RepoFullName+'/issues') -Fields @{title=[string]$title;body=[string]$body}
    }.GetNewClosure()
    $search={
        param($identity)
        $pages=@(Invoke-OrchestratorGhJson -Method 'GET' -Endpoint ('/repos/'+$RepoFullName+'/issues?state=all&per_page=100') -Paginate)
        $issues=@()
        foreach($page in $pages){
            foreach($item in @($page)){
                if($null-ne$item -and (Test-HasProperty $item 'body')){$issues+=,$item}
            }
        }
        return $issues
    }.GetNewClosure()
    $readIssue={
        param($number)
        Invoke-OrchestratorGhJson -Method 'GET' -Endpoint ('/repos/'+$RepoFullName+'/issues/'+[int]$number)
    }.GetNewClosure()
    $readComments={
        param($number)
        $pages=@(Invoke-OrchestratorGhJson -Method 'GET' -Endpoint ('/repos/'+$RepoFullName+'/issues/'+[int]$number+'/comments?per_page=100') -Paginate)
        $comments=@()
        foreach($page in $pages){foreach($item in @($page)){$comments+=,$item}}
        return $comments
    }.GetNewClosure()
    $update={
        param($number,$title,$state)
        Invoke-OrchestratorGhJson -Method 'PATCH' -Endpoint ('/repos/'+$RepoFullName+'/issues/'+[int]$number) -Fields @{title=[string]$title;state=[string]$state}
    }.GetNewClosure()
    return [pscustomobject]@{CreateIssue=$create;SearchIssues=$search;ReadIssue=$readIssue;ReadComments=$readComments;UpdateIssue=$update}
}
