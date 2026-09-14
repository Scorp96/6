param(
    [string]$Repo = "Scorp96/666",
    [int]$PollSeconds = 5,
    [int]$TaskTimeoutSeconds = 1800,
    [int]$HeartbeatSeconds = 300,
    [switch]$SelfTest,
    [switch]$MutexProbe,
    [string]$MutexName = ""
)

$ErrorActionPreference = "Continue"
$ProtocolVersion = "scorp.exec/v4"
$AgentRoot = "C:\ScorpAgent"
$StateDir = Join-Path $AgentRoot "state-v4"
$LogDir = Join-Path $AgentRoot "logs-v4"
$ActionLedgerDir = Join-Path $StateDir "action-ledger"
$RejectionLedgerDir = Join-Path $StateDir "rejection-ledger"
$ActiveTaskPath = Join-Path $StateDir "active-task.json"
$ScriptRoot = Split-Path -Parent $PSCommandPath
$RunnerPath = Join-Path $ScriptRoot "runner-v4.1.ps1"
$QueuedPrefix = "[SCORP_EXEC]"
$RunningPrefix = "[SCORP_EXEC_RUNNING]"
$DonePrefix = "[SCORP_EXEC_DONE]"
$FailedPrefix = "[SCORP_EXEC_FAILED]"
$BlockedPrefix = "[SCORP_EXEC_BLOCKED]"
$TrustedAuthor = "Scorp96"
New-Item -ItemType Directory -Force -Path $StateDir,$LogDir,$ActionLedgerDir,$RejectionLedgerDir | Out-Null
$LogFile = Join-Path $LogDir "executor-v4.log"

function Write-Log {
    param([string]$Message)
    [IO.File]::AppendAllText($LogFile,"$(Get-Date -Format o) $Message$([Environment]::NewLine)",(New-Object Text.UTF8Encoding($false)))
}

function Write-Utf8NoBom {
    param([string]$Path,[string]$Text)
    $dir=Split-Path -Parent $Path
    if($dir){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
    [IO.File]::WriteAllText($Path,$Text,(New-Object Text.UTF8Encoding($false)))
}

function Write-JsonAtomic {
    param([string]$Path,$Value)
    $dir=Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir|Out-Null
    $id=[guid]::NewGuid().ToString("N")
    $tmp=Join-Path $dir (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)),$id)
    $bak=Join-Path $dir (".{0}.{1}.bak" -f ([IO.Path]::GetFileName($Path)),$id)
    try{
        Write-Utf8NoBom -Path $tmp -Text ($Value|ConvertTo-Json -Depth 24)
        if(Test-Path -LiteralPath $Path -PathType Leaf){[IO.File]::Replace($tmp,$Path,$bak,$true)}
        else{[IO.File]::Move($tmp,$Path)}
    }finally{Remove-Item -LiteralPath $tmp,$bak -Force -ErrorAction SilentlyContinue}
}

function Get-StringSha256 {
    param([string]$Text)
    $sha=[Security.Cryptography.SHA256]::Create()
    try{return(([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))))-replace'-','').ToLowerInvariant()}
    finally{$sha.Dispose()}
}

function Get-DefaultMutexName {
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $machine=[string]$env:COMPUTERNAME
    if([string]::IsNullOrWhiteSpace($machine)){$machine=[Environment]::MachineName}
    if([string]::IsNullOrWhiteSpace($machine)){throw "cannot determine machine name for executor mutex"}
    $machine=($machine-replace'[^A-Za-z0-9_.-]','_')
    return "Local\ScorpGptNativeExecutorV4-$machine-$sid"
}

function Enter-Mutex {
    param([string]$Name)
    $created=$false
    $m=New-Object System.Threading.Mutex($false,$Name,[ref]$created)
    $acquired=$false
    try{$acquired=$m.WaitOne(0,$false)}
    catch [System.Threading.AbandonedMutexException]{$acquired=$true}
    return [pscustomobject]@{Mutex=$m;Acquired=[bool]$acquired;CreatedNew=[bool]$created}
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    if($Value.Length-gt0-and$Value-cnotmatch'[\s"]'){return $Value}
    $sb=New-Object Text.StringBuilder
    [void]$sb.Append('"');$slashes=0
    foreach($ch in $Value.ToCharArray()){
        if([int]$ch-eq92){$slashes++;continue}
        if($ch-eq'"'){
            if($slashes-gt0){[void]$sb.Append((('\' * ($slashes*2)) -join ''))}
            [void]$sb.Append('\"');$slashes=0;continue
        }
        if($slashes-gt0){[void]$sb.Append((('\' * $slashes)-join''));$slashes=0}
        [void]$sb.Append($ch)
    }
    if($slashes-gt0){[void]$sb.Append((('\' * ($slashes*2))-join''))}
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Invoke-Gh {
    param([string[]]$Arguments)
    $ghPath=(Get-Command gh.exe -ErrorAction Stop).Source
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$ghPath
    $psi.Arguments=(@($Arguments|ForEach-Object{ConvertTo-NativeArgument -Value ([string]$_)}) -join ' ')
    $psi.UseShellExecute=$false
    $psi.RedirectStandardOutput=$true
    $psi.RedirectStandardError=$true
    $psi.CreateNoWindow=$true
    $utf8=New-Object Text.UTF8Encoding($false)
    $psi.StandardOutputEncoding=$utf8
    $psi.StandardErrorEncoding=$utf8
    $proc=New-Object System.Diagnostics.Process
    $proc.StartInfo=$psi
    try{
        if(-not$proc.Start()){throw "gh process failed to start"}
        $stdoutTask=$proc.StandardOutput.ReadToEndAsync()
        $stderrTask=$proc.StandardError.ReadToEndAsync()
        $proc.WaitForExit()
        $stdout=$stdoutTask.GetAwaiter().GetResult()
        $stderr=$stderrTask.GetAwaiter().GetResult()
        $code=$proc.ExitCode
    }finally{$proc.Dispose()}
    if($code-ne0){throw "gh failed ($code): $stderr"}
    return $stdout
}
function Convert-JsonToFlatArray {
    param([string]$Json)
    if([string]::IsNullOrWhiteSpace($Json)){return @()}
    $parsed=ConvertFrom-Json -InputObject $Json
    if($null-eq$parsed){return @()}
    $items=New-Object System.Collections.ArrayList
    function Add-One($Value){
        if($null-eq$Value){return}
        if($Value-is[System.Array]){foreach($x in $Value){Add-One $x}}
        else{[void]$items.Add($Value)}
    }
    Add-One $parsed
    return @($items)
}

function Normalize-CommentBody {
    param([AllowNull()][string]$Body)
    if($null-eq$Body){return ""}
    return $Body.TrimStart([char[]]@([char]0xFEFF,[char]0x20,[char]0x09,[char]0x0D,[char]0x0A))
}

function Test-TrustedComment {
    param($Comment)
    return ($null-ne$Comment.user -and [string]$Comment.user.login-ceq$TrustedAuthor)
}

function Get-IssueSnapshot {
    param([int]$Number)
    return (Invoke-Gh @("issue","view",$Number,"--repo",$Repo,"--json","number,title,state,body,author")|ConvertFrom-Json)
}

function Get-AuthoritativeComments {
    param([int]$Number)
    return Convert-JsonToFlatArray -Json (Invoke-Gh @("api","--paginate","--slurp","/repos/$Repo/issues/$Number/comments?per_page=100"))
}

function Post-Comment {
    param([int]$Number,[string]$Text)
    $tmp=Join-Path $env:TEMP ("scorp-v4-comment-{0}-{1}.txt"-f$Number,[guid]::NewGuid().ToString("N"))
    try{
        Write-Utf8NoBom -Path $tmp -Text $Text
        Invoke-Gh @("issue","comment",$Number,"--repo",$Repo,"--body-file",$tmp)|Out-Null
    }finally{Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}
}

function Parse-ClaimIdentity {
    param([string]$Body)
    $body=Normalize-CommentBody $Body
    if(-not$body.StartsWith("SCORP_EXEC_CLAIMED",[StringComparison]::Ordinal)){return $null}
    $issue=$null;$task=$null;$action=$null;$claim=$null
    if($body-match'(?m)^Issue:\s*(\d+)\s*$'){$issue=[int]$Matches[1]}
    if($body-match'(?m)^TaskId:\s*([^\r\n]+)\s*$'){$task=$Matches[1].Trim()}
    if($body-match'(?m)^ActionId:\s*([^\r\n]+)\s*$'){$action=$Matches[1].Trim()}
    if($body-match'(?m)^ClaimToken:\s*([^\r\n]+)\s*$'){$claim=$Matches[1].Trim()}
    if($null-eq$issue-or[string]::IsNullOrWhiteSpace($task)-or[string]::IsNullOrWhiteSpace($action)-or[string]::IsNullOrWhiteSpace($claim)){return $null}
    return [pscustomobject]@{Issue=$issue;TaskId=$task;ActionId=$action;ClaimToken=$claim}
}

function Parse-ResultIdentity {
    param([string]$Body)
    $body=Normalize-CommentBody $Body
    if(-not$body.StartsWith("SCORP_EXEC_RESULT",[StringComparison]::Ordinal)){return $null}
    $issue=$null;$task=$null;$action=$null;$claim=$null;$status=$null
    if($body-match'(?m)^Issue:\s*(\d+)\s*$'){$issue=[int]$Matches[1]}
    if($body-match'(?m)^TaskId:\s*([^\r\n]+)\s*$'){$task=$Matches[1].Trim()}
    if($body-match'(?m)^ActionId:\s*([^\r\n]+)\s*$'){$action=$Matches[1].Trim()}
    if($body-match'(?m)^ClaimToken:\s*([^\r\n]+)\s*$'){$claim=$Matches[1].Trim()}
    if($body-match'(?m)^Status:\s*([^\r\n]+)\s*$'){$status=$Matches[1].Trim()}
    if($null-eq$issue-or[string]::IsNullOrWhiteSpace($task)-or[string]::IsNullOrWhiteSpace($action)-or[string]::IsNullOrWhiteSpace($claim)){return $null}
    return [pscustomobject]@{Issue=$issue;TaskId=$task;ActionId=$action;ClaimToken=$claim;Status=$status}
}

function Get-MatchingClaims {
    param($Comments,[int]$IssueNumber,[string]$TaskId,[string]$ActionId,[string]$ClaimToken)
    $out=@()
    foreach($c in @($Comments)){
        if($null-eq$c.user-or[string]$c.user.login-cne$TrustedAuthor){continue}
        $id=Parse-ClaimIdentity ([string]$c.body)
        if($null-ne$id-and$id.Issue-eq$IssueNumber-and$id.TaskId-ceq$TaskId-and$id.ActionId-ceq$ActionId-and$id.ClaimToken-ceq$ClaimToken){$out+=$c}
    }
    return @($out)
}

function Get-MatchingResults {
    param($Comments,[int]$IssueNumber,[string]$TaskId,[string]$ActionId,[string]$ClaimToken)
    $out=@()
    foreach($c in @($Comments)){
        if($null-eq$c.user-or[string]$c.user.login-cne$TrustedAuthor){continue}
        $id=Parse-ResultIdentity ([string]$c.body)
        if($null-ne$id-and$id.Issue-eq$IssueNumber-and$id.TaskId-ceq$TaskId-and$id.ActionId-ceq$ActionId-and$id.ClaimToken-ceq$ClaimToken){$out+=$c}
    }
    return @($out)
}

function Get-MatchingBlockComments {
    param($Comments,$State)
    $out=@();$n=[int]$State.issue_number;$task=[string]$State.task_id;$action=[string]$State.action_id;$claim=[string]$State.claim_token
    foreach($c in @($Comments)){
        if(-not(Test-TrustedComment $c)){continue}
        $body=Normalize-CommentBody([string]$c.body)
        if(-not$body.StartsWith("SCORP_EXEC_BLOCKED",[StringComparison]::Ordinal)){continue}
        $i=$null;$t=$null;$a=$null;$k=$null
        if($body-match'(?m)^Issue:\s*(\d+)\s*$'){$i=[int]$Matches[1]}
        if($body-match'(?m)^TaskId:\s*([^\r\n]+)\s*$'){$t=$Matches[1].Trim()}
        if($body-match'(?m)^ActionId:\s*([^\r\n]+)\s*$'){$a=$Matches[1].Trim()}
        if($body-match'(?m)^ClaimToken:\s*([^\r\n]+)\s*$'){$k=$Matches[1].Trim()}
        if($i-eq$n-and$t-ceq$task-and$a-ceq$action-and$k-ceq$claim){$out+=$c}
    }
    return @($out)
}

function Get-MatchingRejectionComments {
    param($Comments,[int]$IssueNumber,[string]$RejectionId)
    $out=@()
    foreach($c in @($Comments)){
        if(-not(Test-TrustedComment $c)){continue}
        $body=Normalize-CommentBody([string]$c.body)
        if(-not$body.StartsWith("SCORP_EXEC_REJECTED",[StringComparison]::Ordinal)){continue}
        $i=$null;$rid=$null
        if($body-match'(?m)^Issue:\s*(\d+)\s*$'){$i=[int]$Matches[1]}
        if($body-match'(?m)^RejectionId:\s*([^\r\n]+)\s*$'){$rid=$Matches[1].Trim()}
        if($i-eq$IssueNumber-and$rid-ceq$RejectionId){$out+=$c}
    }
    return @($out)
}

function Set-IssueTitleVerified {
    param([int]$Number,[string]$NewTitle)
    Invoke-Gh @("issue","edit",$Number,"--repo",$Repo,"--title",$NewTitle)|Out-Null
    $after=Get-IssueSnapshot $Number
    if([string]$after.title-cne$NewTitle){throw "title verification failed issue #$Number"}
    return $after
}

function Set-IssueTitleFromExpectedVerified {
    param([int]$Number,[string]$ExpectedTitle,[string]$NewTitle)
    $before=Get-IssueSnapshot $Number
    if([string]$before.title-ceq$NewTitle){return $before}
    if([string]$before.title-cne$ExpectedTitle){throw "AMBIGUOUS_REMOTE: issue #$Number title is neither expected title nor target title"}
    Invoke-Gh @("issue","edit",$Number,"--repo",$Repo,"--title",$NewTitle)|Out-Null
    $after=Get-IssueSnapshot $Number
    if([string]$after.title-cne$NewTitle){throw "AMBIGUOUS_REMOTE: expected-title transition verification failed issue #$Number"}
    return $after
}

function Close-IssueVerified {
    param([int]$Number,[string]$Reason="completed")
    $issue=Get-IssueSnapshot $Number
    if([string]$issue.state-ne"CLOSED"){
        Invoke-Gh @("issue","close",$Number,"--repo",$Repo,"--reason",$Reason)|Out-Null
        $issue=Get-IssueSnapshot $Number
    }
    if([string]$issue.state-ne"CLOSED"){throw "close verification failed issue #$Number"}
}

function Validate-Envelope {
    param($Envelope,[int]$IssueNumber)
    if($null-eq$Envelope){throw "envelope missing"}
    if([string]$Envelope.protocol_version-cne$ProtocolVersion){throw "unsupported protocol_version"}
    if([string]::IsNullOrWhiteSpace([string]$Envelope.task_id)){throw "task_id missing"}
    if([string]$Envelope.action_id-notmatch'^[A-Za-z0-9._:-]{8,160}$'){throw "invalid action_id"}
    if([string]$Envelope.action_kind-notin@("powershell","process","file_read","file_write","file_replace_exact","git","health","privileged_broker")){throw "unsupported action_kind"}
    $t=[int]$Envelope.timeout_seconds
    if($t-lt1-or$t-gt1800){throw "timeout_seconds invalid"}
    if([string]$Envelope.safety_class-notin@("standard","approved_admin")){throw "safety_class invalid"}
    if([string]$Envelope.safety_class-ceq"approved_admin"-and[string]$Envelope.authorization-cne"USER_APPROVED_FULL_CONTROL"){throw "approved_admin authorization missing"}
    if($null-eq$Envelope.payload){throw "payload missing"}
    if([string]$Envelope.action_kind-ceq"privileged_broker"){
        if([string]$Envelope.safety_class-cne"approved_admin"){throw "privileged_broker requires approved_admin"}
        if([string]$Envelope.authorization-cne"USER_APPROVED_FULL_CONTROL"){throw "privileged_broker authorization missing"}
        $payloadNames=@($Envelope.payload.PSObject.Properties|ForEach-Object{[string]$_.Name})
        if($payloadNames.Count-ne2-or-not($payloadNames-ccontains"operation")-or-not($payloadNames-ccontains"params")){throw "privileged_broker payload must contain exactly operation and params"}
        if([string]::IsNullOrWhiteSpace([string]$Envelope.payload.operation)){throw "privileged_broker operation missing"}
        if($null-eq$Envelope.payload.params-or$Envelope.payload.params-isnot[pscustomobject]){throw "privileged_broker params must be an object"}
        $allowedBrokerOperations=@("identity.get","service.get","service.restart","task.get","task.run","file.write","registry.set")
        if([string]$Envelope.payload.operation-notin$allowedBrokerOperations){throw "privileged_broker operation not allowed"}
    }
    if($null-ne$Envelope.issue_number-and[int]$Envelope.issue_number-ne$IssueNumber){throw "issue_number mismatch"}
}
function Copy-EnvelopeForExecution {
    param($Envelope,[int]$IssueNumber,[string]$ClaimToken)
    $map=[ordered]@{}
    foreach($p in $Envelope.PSObject.Properties){$map[$p.Name]=$p.Value}
    $map["issue_number"]=$IssueNumber;$map["claim_token"]=$ClaimToken
    return $map
}

function Get-ActionLedgerPath {
    param([string]$TaskId,[string]$ActionId)
    return Join-Path $ActionLedgerDir ((Get-StringSha256("$TaskId`n$ActionId"))+".json")
}

function Reserve-ActionIdentity {
    param([string]$TaskId,[string]$ActionId,[int]$IssueNumber,[string]$EnvelopeSha256)
    if($EnvelopeSha256-notmatch'^[0-9a-f]{64}$'){throw "GLOBAL_IDEMPOTENCY: invalid envelope sha256"}
    $path=Get-ActionLedgerPath $TaskId $ActionId
    if(Test-Path -LiteralPath $path -PathType Leaf){
        $existing=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
        if([string]$existing.task_id-cne$TaskId-or[string]$existing.action_id-cne$ActionId){throw "GLOBAL_IDEMPOTENCY: action ledger hash collision or corruption"}
        if([string]$existing.envelope_sha256-cne$EnvelopeSha256){throw "GLOBAL_IDEMPOTENCY: task/action envelope changed; refusing execution"}
        if([int]$existing.issue_number-ne$IssueNumber){throw "GLOBAL_IDEMPOTENCY: task/action already reserved by issue #$($existing.issue_number)"}
        if([string]$existing.state-in@("TERMINAL","BLOCKED")){throw "GLOBAL_IDEMPOTENCY: task/action already terminal in issue #$IssueNumber"}
        return $existing
    }
    $ledger=[ordered]@{
        protocol_version=$ProtocolVersion;task_id=$TaskId;action_id=$ActionId;issue_number=$IssueNumber
        envelope_sha256=$EnvelopeSha256;claim_token=[guid]::NewGuid().ToString("N");state="RESERVED"
        reserved_at=(Get-Date -Format o);terminal_status=$null;terminal_at=$null
    }
    Write-JsonAtomic $path $ledger
    return [pscustomobject]$ledger
}

function Finalize-ActionLedger {
    param($State,[string]$TerminalStatus)
    $path=if(-not[string]::IsNullOrWhiteSpace([string]$State.action_ledger_path)){[string]$State.action_ledger_path}else{Get-ActionLedgerPath ([string]$State.task_id) ([string]$State.action_id)}
    if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "action ledger missing during terminal finalization"}
    $ledger=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
    if(([string]$ledger.task_id-cne[string]$State.task_id)-or
       ([string]$ledger.action_id-cne[string]$State.action_id)-or
       ([int]$ledger.issue_number-ne[int]$State.issue_number)-or
       ([string]$ledger.claim_token-cne[string]$State.claim_token)-or
       ([string]$ledger.envelope_sha256-cne[string]$State.envelope_sha256)){throw "action ledger identity mismatch"}
    $ledger.state=if($TerminalStatus-ceq"BLOCKED"){"BLOCKED"}else{"TERMINAL"}
    $ledger.terminal_status=$TerminalStatus;$ledger.terminal_at=(Get-Date -Format o)
    Write-JsonAtomic $path $ledger
}

function Get-QueuedIssue {
    $issues=Convert-JsonToFlatArray -Json (Invoke-Gh @("issue","list","--repo",$Repo,"--state","open","--limit","100","--json","number,title,author,body,createdAt"))
    $eligible=@()
    foreach($issue in @($issues)){
        $n=0
        if(-not[int]::TryParse([string]$issue.number,[ref]$n)){continue}
        if(([string]$issue.title).StartsWith($QueuedPrefix,[StringComparison]::Ordinal)-and[string]$issue.author.login-ceq$TrustedAuthor){$eligible+=$issue}
    }
    return @($eligible|Sort-Object{[int]$_.number}|Select-Object -First 1)[0]
}

function Save-State {param($State);Write-JsonAtomic $ActiveTaskPath $State}
function Read-State {if(-not(Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf)){return $null};return(Get-Content -LiteralPath $ActiveTaskPath -Raw|ConvertFrom-Json)}
function Clear-State {Remove-Item -LiteralPath $ActiveTaskPath -Force -ErrorAction SilentlyContinue;Write-Log "cleared active state"}

function Test-ChildAlive {
    param($State)
    $pidValue=0
    if(-not[int]::TryParse([string]$State.child_pid,[ref]$pidValue)-or$pidValue-le0){return $false}
    try{$p=Get-CimInstance Win32_Process -Filter ("ProcessId = {0}"-f$pidValue) -ErrorAction Stop}
    catch{throw "AMBIGUOUS_LOCAL: child identity discovery failed for pid=${pidValue}: $($_.Exception.Message)"}
    if($null-eq$p){return $false}
    if([string]$p.Name-notin@("powershell.exe","pwsh.exe")){return $false}
    $cmd=[string]$p.CommandLine
    if([string]::IsNullOrWhiteSpace($cmd)){throw "AMBIGUOUS_LOCAL: child command line unavailable for pid=$pidValue"}
    if($cmd.IndexOf("runner-v4.1.ps1",[StringComparison]::OrdinalIgnoreCase)-lt0){return $false}
    foreach($required in @([string]$State.gate_path,[string]$State.envelope_path,[string]$State.result_path)){
        if([string]::IsNullOrWhiteSpace($required)){return $false}
        if($cmd.IndexOf($required,[StringComparison]::OrdinalIgnoreCase)-lt0){return $false}
    }
    if(-not[string]::IsNullOrWhiteSpace([string]$State.child_started_at)){
        try{
            $expected=[DateTimeOffset]::Parse([string]$State.child_started_at)
            $gp=Get-Process -Id $pidValue -ErrorAction Stop
            $actual=[DateTimeOffset]$gp.StartTime
        }catch{throw "AMBIGUOUS_LOCAL: child start-time authority failed for pid=${pidValue}: $($_.Exception.Message)"}
        if([Math]::Abs(($actual-$expected).TotalSeconds)-gt5){return $false}
    }
    return $true
}

function Test-MonitorChildAlive {
    param($State)
    if(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf){return $false}
    try{return [bool](Test-ChildAlive $State)}
    catch{
        if(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf){return $false}
        throw
    }
}
function Find-RunnerProcesses {
    param($State)
    $gate=[string]$State.gate_path;$envPath=[string]$State.envelope_path;$resultPath=[string]$State.result_path
    if([string]::IsNullOrWhiteSpace($gate)-or[string]::IsNullOrWhiteSpace($envPath)-or[string]::IsNullOrWhiteSpace($resultPath)){throw "AMBIGUOUS_LOCAL: runner discovery identity fields missing"}
    try{$procs=Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction Stop}
    catch{Write-Log "runner process discovery failed: $($_.Exception.Message)";throw "AMBIGUOUS_LOCAL: runner process discovery authority failed: $($_.Exception.Message)"}
    $matches=@()
    foreach($p in @($procs)){
        $cmd=[string]$p.CommandLine
        if([string]::IsNullOrWhiteSpace($cmd)){continue}
        if($cmd.IndexOf("runner-v4.1.ps1",[StringComparison]::OrdinalIgnoreCase)-ge0-and
           $cmd.IndexOf($gate,[StringComparison]::OrdinalIgnoreCase)-ge0-and
           $cmd.IndexOf($envPath,[StringComparison]::OrdinalIgnoreCase)-ge0-and
           $cmd.IndexOf($resultPath,[StringComparison]::OrdinalIgnoreCase)-ge0){$matches+=$p}
    }
    return @($matches)
}

function Ensure-BrokerReconciliationChild {
    param($State)
    if([string]$State.action_kind-cne"privileged_broker"){throw "AMBIGUOUS_LOCAL: broker reconciliation requested for non-broker action"}
    if(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf){return}
    if(Test-ChildAlive $State){
        if(-not(Test-Path -LiteralPath ([string]$State.gate_path) -PathType Leaf)){Write-Utf8NoBom ([string]$State.gate_path) "GO"}
        return
    }
    $found=@(Find-RunnerProcesses $State)
    if($found.Count-gt1){throw "AMBIGUOUS_LOCAL: multiple gated runner processes found during broker reconciliation"}
    if($found.Count-eq1){
        $p=$found[0];$State.child_pid=[int]$p.ProcessId
        try{$gp=Get-Process -Id $State.child_pid -ErrorAction Stop;$State.child_started_at=([DateTimeOffset]$gp.StartTime).ToString("o")}
        catch{throw "AMBIGUOUS_LOCAL: broker reconciliation adopted runner start-time authority failed: $($_.Exception.Message)"}
        $State.stage="RUNNING";Save-State $State
        if(-not(Test-ChildAlive $State)){throw "AMBIGUOUS_LOCAL: broker reconciliation adopted runner failed authoritative identity verification"}
        if(-not(Test-Path -LiteralPath ([string]$State.gate_path) -PathType Leaf)){Write-Utf8NoBom ([string]$State.gate_path) "GO"}
        Write-Log "adopted broker reconciliation runner issue #$($State.issue_number) pid=$($State.child_pid)"
        return
    }
    if([int]$State.broker_reconcile_attempts-ge1){throw "AMBIGUOUS_LOCAL: privileged broker reconciliation retry already consumed"}
    $State.broker_reconcile_attempts=[int]$State.broker_reconcile_attempts+1
    Save-State $State
    Write-Log "starting broker reconciliation issue #$($State.issue_number) attempt=$($State.broker_reconcile_attempts)"
    Launch-GatedChild $State
}
function Ensure-ClaimExactlyOnce {
    param($State)
    $n=[int]$State.issue_number;$task=[string]$State.task_id
    $matches=@(Get-MatchingClaims (Get-AuthoritativeComments $n) $n $task ([string]$State.action_id) ([string]$State.claim_token))
    if($matches.Count-gt1){throw "duplicate matching claims"}
    if($matches.Count-eq1){$State.claim_phase="VERIFIED";Save-State $State;return}
    if([string]$State.claim_phase-in@("POSTING","POSTED_UNVERIFIED")){
        for($i=0;$i-lt5;$i++){
            Start-Sleep -Milliseconds (400*[Math]::Pow(2,$i))
            $matches=@(Get-MatchingClaims (Get-AuthoritativeComments $n) $n $task ([string]$State.action_id) ([string]$State.claim_token))
            if($matches.Count-gt1){throw "duplicate matching claims"}
            if($matches.Count-eq1){$State.claim_phase="VERIFIED";Save-State $State;return}
        }
        throw "AMBIGUOUS_CLAIM: prior claim publication is unresolved; refusing repost"
    }
    $State.claim_phase="POSTING";Save-State $State
    $text="SCORP_EXEC_CLAIMED`nProtocol: $ProtocolVersion`nIssue: $n`nTaskId: $task`nActionId: $($State.action_id)`nClaimToken: $($State.claim_token)`nHost: $env:COMPUTERNAME`nTime: $(Get-Date -Format o)"
    try{Post-Comment $n $text;$State.claim_phase="POSTED_UNVERIFIED";Save-State $State}
    catch{Write-Log "claim post error issue #${n}: $($_.Exception.Message)"}
    for($i=0;$i-lt6;$i++){
        Start-Sleep -Milliseconds (300*[Math]::Pow(2,$i))
        $matches=@(Get-MatchingClaims (Get-AuthoritativeComments $n) $n $task ([string]$State.action_id) ([string]$State.claim_token))
        if($matches.Count-gt1){throw "duplicate matching claims"}
        if($matches.Count-eq1){$State.claim_phase="VERIFIED";Save-State $State;return}
    }
    throw "AMBIGUOUS_CLAIM: claim could not be authoritatively verified"
}

function Launch-GatedChild {
    param($State)
    Remove-Item -LiteralPath ([string]$State.gate_path),([string]$State.result_path) -Force -ErrorAction SilentlyContinue
    $args=@("-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",$RunnerPath,
        "-EnvelopePath",[string]$State.envelope_path,"-ResultPath",[string]$State.result_path,
        "-LogPath",[string]$State.runner_log_path,"-StartGatePath",[string]$State.gate_path,"-GateWaitSeconds","60")
    $child=Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $args -PassThru -WindowStyle Hidden
    Start-Sleep -Milliseconds 100;$child.Refresh()
    $State.child_pid=[int]$child.Id;$State.child_started_at=([DateTimeOffset]$child.StartTime).ToString("o");$State.stage="RUNNING";Save-State $State
    if(-not(Test-ChildAlive $State)){throw "AMBIGUOUS_LOCAL: launched child failed authoritative identity verification"}
    Write-Utf8NoBom -Path ([string]$State.gate_path) -Text "GO"
    Write-Log "launched issue #$($State.issue_number) action=$($State.action_id) pid=$($State.child_pid)"
}

function Ensure-ChildStarted {
    param($State)
    if(Test-ChildAlive $State){
        if(-not(Test-Path -LiteralPath ([string]$State.gate_path) -PathType Leaf)){Write-Utf8NoBom ([string]$State.gate_path) "GO"}
        return
    }
    $found=@(Find-RunnerProcesses $State)
    if($found.Count-gt1){throw "AMBIGUOUS_LOCAL: multiple gated runner processes found"}
    if($found.Count-eq1){
        $p=$found[0];$State.child_pid=[int]$p.ProcessId
        try{$gp=Get-Process -Id $State.child_pid -ErrorAction Stop;$State.child_started_at=([DateTimeOffset]$gp.StartTime).ToString("o")}
        catch{throw "AMBIGUOUS_LOCAL: adopted runner start-time authority failed: $($_.Exception.Message)"}
        $State.stage="RUNNING";Save-State $State
        if(-not(Test-ChildAlive $State)){throw "AMBIGUOUS_LOCAL: adopted runner failed authoritative identity verification"}
        if(-not(Test-Path -LiteralPath ([string]$State.gate_path) -PathType Leaf)){Write-Utf8NoBom ([string]$State.gate_path) "GO"}
        Write-Log "adopted pre-record child issue #$($State.issue_number) pid=$($State.child_pid)"
        return
    }
    if(Test-Path -LiteralPath ([string]$State.gate_path) -PathType Leaf){throw "AMBIGUOUS_LOCAL: start gate exists but no live child/result"}
    Launch-GatedChild $State
}

function New-ResultReport {
    param($State,$Result)
    $rp=[string]$State.result_path
    $sha=if(Test-Path -LiteralPath $rp -PathType Leaf){(Get-FileHash -LiteralPath $rp -Algorithm SHA256).Hash.ToLowerInvariant()}else{"missing"}
    $tail=""
    if($null-ne$Result.evidence-and$null-ne$Result.evidence.combined_tail){$tail=[string]$Result.evidence.combined_tail}
    if($tail.Length-gt6000){$tail="[tail truncated]`n"+$tail.Substring($tail.Length-6000)}
    return "SCORP_EXEC_RESULT`nProtocol: $ProtocolVersion`nIssue: $($State.issue_number)`nTaskId: $($State.task_id)`nActionId: $($State.action_id)`nClaimToken: $($State.claim_token)`nStatus: $($Result.status)`nExitCode: $($Result.exit_code)`nStartedAt: $($Result.started_at)`nFinishedAt: $($Result.finished_at)`nResultPath: $rp`nResultSHA256: $sha`n`n$tail"
}

function Publish-ResultExactlyOnce {
    param($State,$Result)
    $n=[int]$State.issue_number;$task=[string]$State.task_id
    $matches=@(Get-MatchingResults (Get-AuthoritativeComments $n) $n $task ([string]$State.action_id) ([string]$State.claim_token))
    if($matches.Count-gt1){throw "duplicate matching results"}
    if($matches.Count-eq1){$State.result_phase="VERIFIED";Save-State $State;return}
    if([string]$State.result_phase-in@("POSTING","POSTED_UNVERIFIED")){
        for($i=0;$i-lt5;$i++){
            Start-Sleep -Milliseconds (500*[Math]::Pow(2,$i))
            $matches=@(Get-MatchingResults (Get-AuthoritativeComments $n) $n $task ([string]$State.action_id) ([string]$State.claim_token))
            if($matches.Count-gt1){throw "duplicate matching results"}
            if($matches.Count-eq1){$State.result_phase="VERIFIED";Save-State $State;return}
        }
        throw "AMBIGUOUS_POST: prior result publication is unresolved; refusing repost"
    }
    $State.result_phase="POSTING";Save-State $State
    try{Post-Comment $n (New-ResultReport $State $Result);$State.result_phase="POSTED_UNVERIFIED";Save-State $State}
    catch{Write-Log "result post error issue #${n}: $($_.Exception.Message)"}
    for($i=0;$i-lt6;$i++){
        Start-Sleep -Milliseconds (400*[Math]::Pow(2,$i))
        $matches=@(Get-MatchingResults (Get-AuthoritativeComments $n) $n $task ([string]$State.action_id) ([string]$State.claim_token))
        if($matches.Count-gt1){throw "duplicate matching results"}
        if($matches.Count-eq1){$State.result_phase="VERIFIED";Save-State $State;return}
    }
    throw "AMBIGUOUS_POST: result could not be authoritatively verified"
}

function Ensure-BlockedIncidentExactlyOnce {
    param($State)
    $n=[int]$State.issue_number
    $matches=@(Get-MatchingBlockComments (Get-AuthoritativeComments $n) $State)
    if($matches.Count-gt1){throw "AMBIGUOUS_BLOCK: duplicate block incident comments"}
    if($matches.Count-eq1){$State.block_phase="VERIFIED";Save-State $State;return}
    if([string]$State.block_phase-in@("POSTING","POSTED_UNVERIFIED")){
        for($i=0;$i-lt4;$i++){
            Start-Sleep -Milliseconds (400*[Math]::Pow(2,$i))
            $matches=@(Get-MatchingBlockComments (Get-AuthoritativeComments $n) $State)
            if($matches.Count-gt1){throw "AMBIGUOUS_BLOCK: duplicate block incident comments"}
            if($matches.Count-eq1){$State.block_phase="VERIFIED";Save-State $State;return}
        }
        throw "AMBIGUOUS_BLOCK: prior block incident publication unresolved; refusing repost"
    }
    $State.block_phase="POSTING";Save-State $State
    $text="SCORP_EXEC_BLOCKED`nProtocol: $ProtocolVersion`nIssue: $n`nTaskId: $($State.task_id)`nActionId: $($State.action_id)`nClaimToken: $($State.claim_token)`nReason: $($State.block_reason)`nTime: $(Get-Date -Format o)"
    try{Post-Comment $n $text;$State.block_phase="POSTED_UNVERIFIED";Save-State $State}
    catch{Write-Log "block incident post error issue #${n}: $($_.Exception.Message)"}
    for($i=0;$i-lt5;$i++){
        Start-Sleep -Milliseconds (400*[Math]::Pow(2,$i))
        $matches=@(Get-MatchingBlockComments (Get-AuthoritativeComments $n) $State)
        if($matches.Count-gt1){throw "AMBIGUOUS_BLOCK: duplicate block incident comments"}
        if($matches.Count-eq1){$State.block_phase="VERIFIED";Save-State $State;return}
    }
    throw "AMBIGUOUS_BLOCK: block incident could not be authoritatively verified"
}

function Finalize-State {
    param($State)
    $n=[int]$State.issue_number
    if(-not(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf)){throw "result file missing"}
    try{$result=Get-Content -LiteralPath ([string]$State.result_path) -Raw -Encoding UTF8|ConvertFrom-Json}catch{throw "AMBIGUOUS_LOCAL: invalid durable result JSON path=$($State.result_path): $($_.Exception.Message)"}
    if([string]$result.protocol_version-cne$ProtocolVersion-or
       [string]$result.task_id-cne[string]$State.task_id-or
       [string]$result.action_id-cne[string]$State.action_id-or
       [string]$result.claim_token-cne[string]$State.claim_token-or
       [int]$result.issue_number-ne$n){throw "result identity mismatch"}
    if($null-ne$result.evidence-and$null-ne$result.evidence.runner_pid-and[string]$result.status-cne"TIMED_OUT"){
        if([int]$result.evidence.runner_pid-ne[int]$State.child_pid){throw "result runner_pid identity mismatch"}
    }
    $status=[string]$result.status;$prefix=$null;$reason="completed"
    switch($status){
        "SUCCEEDED"{$prefix=$DonePrefix}
        "FAILED"{$prefix=$FailedPrefix}
        "PRECONDITION_FAILED"{$prefix=$FailedPrefix}
        "TIMED_OUT"{$prefix=$FailedPrefix}
        "BLOCKED"{$prefix=$BlockedPrefix;$reason="not planned"}
        default{throw "unknown terminal result status: $status"}
    }
    Publish-ResultExactlyOnce $State $result
    Set-IssueTitleVerified $n ($prefix+" "+[string]$State.task_id+" "+[string]$State.action_id)|Out-Null
    Close-IssueVerified $n $reason
    Finalize-ActionLedger $State $status
    Write-Log "completed issue #$n action=$($State.action_id) status=$status"
    Clear-State
}

function Block-State {
    param($State,[string]$Reason)
    $n=[int]$State.issue_number
    if([string]::IsNullOrWhiteSpace([string]$State.block_reason)){$State.block_reason=$Reason}
    if([string]::IsNullOrWhiteSpace([string]$State.blocked_at)){$State.blocked_at=(Get-Date -Format o)}
    $State.stage="BLOCKED_PENDING_REMOTE";Save-State $State
    Ensure-BlockedIncidentExactlyOnce $State
    $target=$BlockedPrefix+" "+[string]$State.task_id+" "+[string]$State.action_id
    $current=Get-IssueSnapshot $n
    $allowed=@([string]$State.original_title,[string]$State.running_title,$target)
    if([string]$current.title-cne$target){
        if(-not($allowed-ccontains[string]$current.title)){throw "AMBIGUOUS_REMOTE: refusing to overwrite unexpected issue title while blocking #$n"}
        Set-IssueTitleVerified $n $target|Out-Null
    }
    $verified=Get-IssueSnapshot $n
    if([string]$verified.title-cne$target){throw "AMBIGUOUS_REMOTE: blocked title verification failed issue #$n"}
    Close-IssueVerified $n "not planned"
    $archive=Join-Path $StateDir ("blocked-{0}-{1}.json"-f$n,([string]$State.action_id-replace'[^A-Za-z0-9_.-]','_'))
    Write-JsonAtomic $archive $State
    Finalize-ActionLedger $State "BLOCKED"
    Write-Log "blocked issue #$n action=$($State.action_id) reason=$($State.block_reason)"
    Clear-State
}

function Monitor-State {
    param($State)
    $started=[DateTimeOffset]::Parse([string]$State.started_at)
    $next=$started.AddSeconds($HeartbeatSeconds)
    while(Test-MonitorChildAlive $State){
        Start-Sleep -Seconds 2
        $now=[DateTimeOffset]::Now
        if($now-ge$next){
            $elapsed=[int]($now-$started).TotalSeconds
            try{Post-Comment ([int]$State.issue_number) "SCORP_EXEC_HEARTBEAT`nProtocol: $ProtocolVersion`nIssue: $($State.issue_number)`nTaskId: $($State.task_id)`nActionId: $($State.action_id)`nClaimToken: $($State.claim_token)`nElapsedSeconds: $elapsed`nTime: $(Get-Date -Format o)"}
            catch{Write-Log "heartbeat error: $($_.Exception.Message)"}
            $State.last_heartbeat_at=$now.ToString("o");Save-State $State;$next=$now.AddSeconds($HeartbeatSeconds)
        }
        $limit=[Math]::Min([int]$State.timeout_seconds,$TaskTimeoutSeconds)
        if(($now-$started).TotalSeconds-ge$limit){
            if(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf){break}
            if(-not(Test-MonitorChildAlive $State)){break}
            & taskkill.exe /PID ([int]$State.child_pid) /T /F 2>&1|ForEach-Object{Write-Log([string]$_)}
            Start-Sleep -Seconds 1
            if(-not(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf)){
                Write-JsonAtomic ([string]$State.result_path) ([ordered]@{
                    protocol_version=$ProtocolVersion;task_id=$State.task_id;issue_number=[int]$State.issue_number
                    claim_token=$State.claim_token;action_id=$State.action_id;action_kind=$State.action_kind
                    status="TIMED_OUT";exit_code=124;message="executor timeout";started_at=$State.started_at
                    finished_at=(Get-Date -Format o);duration_ms=[int64](($now-$started).TotalMilliseconds)
                    evidence=[ordered]@{};runner_log_path=$State.runner_log_path
                })
            }
            break
        }
    }
    if(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf){Finalize-State $State}
    elseif([string]$State.action_kind-ceq"privileged_broker"-and[int]$State.broker_reconcile_attempts-lt1){
        Ensure-BrokerReconciliationChild $State
        Monitor-State $State
    }
    else{Block-State $State "child exited without durable result; action outcome ambiguous"}
}

function Resume-State {
    param($State)
    try{
        if([string]$State.stage-ceq"BLOCKED_PENDING_REMOTE"){Block-State $State ([string]$State.block_reason);return}
        if([string]$State.stage-ceq"PREPARED"){
            Set-IssueTitleFromExpectedVerified ([int]$State.issue_number) ([string]$State.original_title) ([string]$State.running_title)|Out-Null
            $State.stage="RUNNING_TITLE_VERIFIED";Save-State $State
        }
        if([string]$State.stage-ceq"RUNNING_TITLE_VERIFIED"){
            Ensure-ClaimExactlyOnce $State;$State.stage="CLAIM_VERIFIED";Save-State $State
        }
        if([string]$State.stage-ceq"CLAIM_VERIFIED"){Ensure-ChildStarted $State}
        if([string]$State.stage-ceq"RUNNING"){
            if(Test-ChildAlive $State){
                if(-not(Test-Path -LiteralPath ([string]$State.gate_path) -PathType Leaf)){Write-Utf8NoBom ([string]$State.gate_path) "GO"}
                Monitor-State $State;return
            }
            if(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf){Finalize-State $State;return}
            if(-not(Test-Path -LiteralPath ([string]$State.gate_path) -PathType Leaf)){Ensure-ChildStarted $State;Monitor-State $State;return}
            if([string]$State.action_kind-ceq"privileged_broker"){
                Ensure-BrokerReconciliationChild $State
                Monitor-State $State;return
            }
            throw "AMBIGUOUS_LOCAL: recorded child is gone, gate exists, and no durable result exists"
        }
        if(Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf){Finalize-State $State;return}
    }catch{
        if([string]$State.stage-ceq"BLOCKED_PENDING_REMOTE"){throw}
        if($_.Exception.Message.StartsWith("AMBIGUOUS_",[StringComparison]::Ordinal)-or
           $_.Exception.Message.StartsWith("GLOBAL_IDEMPOTENCY:",[StringComparison]::Ordinal)){Block-State $State $_.Exception.Message;return}
        throw
    }
}

function Prepare-QueuedIssue {
    param($Issue)
    $n=[int]$Issue.number;$title=[string]$Issue.title;$body=[string]$Issue.body
    $envelope=$body|ConvertFrom-Json
    Validate-Envelope $envelope $n
    $envelopeSha=Get-StringSha256 $body
    foreach($c in @(Get-AuthoritativeComments $n)){
        if(-not(Test-TrustedComment $c)){continue}
        $id=Parse-ResultIdentity([string]$c.body)
        if($null-ne$id-and$id.Issue-eq$n-and$id.TaskId-ceq[string]$envelope.task_id-and$id.ActionId-ceq[string]$envelope.action_id){throw "existing result for task/action; refusing execution"}
    }
    $reservation=Reserve-ActionIdentity ([string]$envelope.task_id) ([string]$envelope.action_id) $n $envelopeSha
    $claim=[string]$reservation.claim_token;$safe=([string]$envelope.action_id-replace'[^A-Za-z0-9_.-]','_')
    $envPath=Join-Path $StateDir("issue-{0}-action-{1}-envelope.json"-f$n,$safe)
    $resultPath=Join-Path $StateDir("issue-{0}-action-{1}-result.json"-f$n,$safe)
    $logPath=Join-Path $LogDir("issue-{0}-action-{1}-runner.log"-f$n,$safe)
    $gatePath=Join-Path $StateDir("issue-{0}-action-{1}-gate.txt"-f$n,$safe)
    Remove-Item -LiteralPath $resultPath,$gatePath -Force -ErrorAction SilentlyContinue
    $execEnvelope=Copy-EnvelopeForExecution $envelope $n $claim
    Write-JsonAtomic $envPath $execEnvelope
    $started=[DateTimeOffset]::Now
    $runningTitle=$RunningPrefix+$title.Substring($QueuedPrefix.Length)
    $ledgerPath=Get-ActionLedgerPath ([string]$envelope.task_id) ([string]$envelope.action_id)
    $state=[ordered]@{
        protocol_version=$ProtocolVersion;issue_number=$n;original_title=$title;running_title=$runningTitle
        task_id=[string]$envelope.task_id;action_id=[string]$envelope.action_id;action_kind=[string]$envelope.action_kind
        envelope_sha256=$envelopeSha;claim_token=$claim;action_ledger_path=$ledgerPath;timeout_seconds=[int]$envelope.timeout_seconds
        envelope_path=$envPath;result_path=$resultPath;runner_log_path=$logPath;gate_path=$gatePath
        child_pid=0;child_started_at=$null;broker_reconcile_attempts=0;started_at=$started.ToString("o");last_heartbeat_at=$started.ToString("o")
        stage="PREPARED";claim_phase=$null;result_phase=$null;block_phase=$null;block_reason=$null;blocked_at=$null
    }
    Save-State $state
    Set-IssueTitleFromExpectedVerified $n $title $runningTitle|Out-Null
    $state.stage="RUNNING_TITLE_VERIFIED";Save-State $state
    Write-Log "prepared issue #$n task=$($state.task_id) action=$($state.action_id)"
    Resume-State ([pscustomobject]$state)
}

function Get-RejectionLedgerPath {
    param([int]$IssueNumber)
    $repoKey=Get-StringSha256(([string]$Repo).ToLowerInvariant())
    return Join-Path $RejectionLedgerDir (("repo-{0}-issue-{1}.json"-f$repoKey,$IssueNumber))
}

function Reject-QueuedIssue {
    param($Issue,[string]$Reason)
    $n=[int]$Issue.number;$title=[string]$Issue.title
    $path=Get-RejectionLedgerPath $n
    $target=$BlockedPrefix+$title.Substring($QueuedPrefix.Length)
    if(Test-Path -LiteralPath $path -PathType Leaf){$ledger=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json}
    else{
        $ledger=[pscustomobject][ordered]@{
            protocol_version=$ProtocolVersion;issue_number=$n;original_title=$title;target_title=$target;reason=$Reason
            rejection_id=("reject-{0}"-f$n);rejection_phase=$null;created_at=(Get-Date -Format o)
            terminal_verified=$false;terminal_verified_at=$null
        }
        Write-JsonAtomic $path $ledger
    }
    $matches=@(Get-MatchingRejectionComments (Get-AuthoritativeComments $n) $n ([string]$ledger.rejection_id))
    if($matches.Count-gt1){$ledger.rejection_phase="AMBIGUOUS_DUPLICATE";Write-JsonAtomic $path $ledger;throw "rejection comment duplicate incident issue #$n"}
    if($matches.Count-eq1){$ledger.rejection_phase="VERIFIED";Write-JsonAtomic $path $ledger}
    elseif([string]::IsNullOrWhiteSpace([string]$ledger.rejection_phase)){
        $ledger.rejection_phase="POSTING";Write-JsonAtomic $path $ledger
        $text="SCORP_EXEC_REJECTED`nProtocol: $ProtocolVersion`nIssue: $n`nRejectionId: $($ledger.rejection_id)`nReason: $($ledger.reason)`nTime: $(Get-Date -Format o)"
        try{Post-Comment $n $text;$ledger.rejection_phase="POSTED_UNVERIFIED"}
        catch{$ledger.rejection_phase="AMBIGUOUS_NO_REPOST";Write-Log "rejection comment publication ambiguous issue #${n}: $($_.Exception.Message)"}
        Write-JsonAtomic $path $ledger
        if([string]$ledger.rejection_phase-ceq"POSTED_UNVERIFIED"){
            $matches=@(Get-MatchingRejectionComments (Get-AuthoritativeComments $n) $n ([string]$ledger.rejection_id))
            if($matches.Count-eq1){$ledger.rejection_phase="VERIFIED"}
            elseif($matches.Count-gt1){$ledger.rejection_phase="AMBIGUOUS_DUPLICATE"}
            else{$ledger.rejection_phase="AMBIGUOUS_NO_REPOST"}
            Write-JsonAtomic $path $ledger
        }
    }
    Set-IssueTitleFromExpectedVerified $n ([string]$ledger.original_title) ([string]$ledger.target_title)|Out-Null
    Close-IssueVerified $n "not planned"
    $ledger.terminal_verified=$true;$ledger.terminal_verified_at=(Get-Date -Format o)
    Write-JsonAtomic $path $ledger
    Write-Log "rejected issue #$n reason=$($ledger.reason) phase=$($ledger.rejection_phase)"
}

function Assert-Test {param([bool]$Condition,[string]$Name);if(-not$Condition){throw "SELFTEST FAIL: $Name"};Write-Output "PASS $Name"}

function Invoke-SelfTest {
    Assert-Test ((Convert-JsonToFlatArray '[{"n":1},{"n":2}]').Count-eq2) "json-flat-array"
    Assert-Test ((Convert-JsonToFlatArray '[[{"n":1}],[{"n":2}]]').Count-eq2) "json-nested-array"
    Assert-Test ((Normalize-CommentBody (([char]0xFEFF)+" `t`r`nSCORP_EXEC_RESULT"))-ceq"SCORP_EXEC_RESULT") "safe-prefix-normalization"
    $e=[pscustomobject]@{protocol_version=$ProtocolVersion;task_id="task";issue_number=$null;claim_token=$null;action_id="action-0001";action_kind="health";timeout_seconds=30;safety_class="standard";authorization=$null;payload=[pscustomobject]@{}}
    Validate-Envelope $e 7
    $m=Copy-EnvelopeForExecution $e 7 "claim-0001"
    Assert-Test ($m.issue_number-eq7-and$m.claim_token-ceq"claim-0001") "inject-runtime-identity"
    $claimBody="SCORP_EXEC_CLAIMED`nIssue: 7`nTaskId: task`nActionId: action-0001`nClaimToken: claim-0001"
    $ci=Parse-ClaimIdentity $claimBody
    Assert-Test ($ci.Issue-eq7-and$ci.TaskId-ceq"task"-and$ci.ActionId-ceq"action-0001") "claim-identity"
    $resultBody="SCORP_EXEC_RESULT`nIssue: 7`nTaskId: task`nActionId: action-0001`nClaimToken: claim-0001`nStatus: SUCCEEDED"
    $ri=Parse-ResultIdentity $resultBody
    Assert-Test ($ri.Issue-eq7-and$ri.TaskId-ceq"task"-and$ri.ClaimToken-ceq"claim-0001") "result-identity"
    $trusted=[pscustomobject]@{body=$resultBody;user=[pscustomobject]@{login=$TrustedAuthor}}
    $untrusted=[pscustomobject]@{body=$resultBody;user=[pscustomobject]@{login="other"}}
    Assert-Test (@(Get-MatchingResults @($trusted) 7 "task" "action-0001" "claim-0001").Count-eq1) "matching-result"
    Assert-Test (@(Get-MatchingResults @($untrusted) 7 "task" "action-0001" "claim-0001").Count-eq0) "untrusted-result-ignored"
    Assert-Test (@(Get-MatchingResults @($trusted) 7 "other-task" "action-0001" "claim-0001").Count-eq0) "task-mismatch-rejected"
    $hash=Get-StringSha256 "envelope"
    Assert-Test ($hash.Length-eq64) "envelope-sha256"
    $selfText=Get-Content -LiteralPath $PSCommandPath -Raw
    $runnerText=Get-Content -LiteralPath $RunnerPath -Raw
    Assert-Test (-not($selfText-match'(?i)codex\s+exec|codex\.exe')) "executor-no-codex-invocation"
    Assert-Test (-not($runnerText-match'(?i)codex\s+exec|codex\.exe')) "runner-no-codex-invocation"
    $prepareStart=$selfText.IndexOf("function Prepare-QueuedIssue",[StringComparison]::Ordinal)
    $rejectStart=$selfText.IndexOf("function Reject-QueuedIssue",$prepareStart,[StringComparison]::Ordinal)
    $prepareText=$selfText.Substring($prepareStart,$rejectStart-$prepareStart)
    Assert-Test ($prepareText.IndexOf("Save-State `$state",[StringComparison]::Ordinal)-lt$prepareText.IndexOf("Set-IssueTitleFromExpectedVerified",[StringComparison]::Ordinal)) "state-before-remote-title"
    $tmp=Join-Path $env:TEMP("scorp-v4-selftest-"+[guid]::NewGuid().ToString("N")+".txt")
    try{
        Write-Utf8NoBom $tmp "abc"
        $b=[IO.File]::ReadAllBytes($tmp)
        Assert-Test (-not($b.Length-ge3-and$b[0]-eq0xEF-and$b[1]-eq0xBB-and$b[2]-eq0xBF)) "utf8-no-bom"
    }finally{Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}
    Write-Output "SELFTEST PASS"
}

if([string]::IsNullOrWhiteSpace($MutexName)){$MutexName=Get-DefaultMutexName}
$guard=Enter-Mutex $MutexName
if(-not$guard.Acquired){
    Write-Log "second executor instance denied mutex=$MutexName"
    if($MutexProbe){Write-Output "MUTEX_DENIED"}
    exit 0
}
try{
    if($MutexProbe){Write-Output "MUTEX_ACQUIRED";exit 0}
    if($SelfTest){Invoke-SelfTest;exit 0}
    if(-not(Test-Path -LiteralPath $RunnerPath -PathType Leaf)){throw "runner-v4.1.ps1 missing: $RunnerPath"}
    Write-Log "executor v4.1 starting repo=$Repo poll=${PollSeconds}s mutex=$MutexName"
    while($true){
        try{
            $state=Read-State
            if($null-ne$state){Resume-State $state;Start-Sleep -Milliseconds 200;continue}
            $issue=Get-QueuedIssue
            if($null-eq$issue){Start-Sleep -Seconds $PollSeconds;continue}
            try{Prepare-QueuedIssue $issue}
            catch{
                if(Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf){throw}
                Reject-QueuedIssue $issue $_.Exception.Message
            }
        }catch{Write-Log "loop error: $($_.Exception.Message)";Start-Sleep -Seconds $PollSeconds}
    }
}finally{
    if($guard.Acquired){try{$guard.Mutex.ReleaseMutex()}catch{}}
    $guard.Mutex.Dispose()
}
