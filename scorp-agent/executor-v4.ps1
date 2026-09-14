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
$ActiveTaskPath = Join-Path $StateDir "active-task.json"
$ScriptRoot = Split-Path -Parent $PSCommandPath
$RunnerPath = Join-Path $ScriptRoot "runner-v4.ps1"
$QueuedPrefix = "[SCORP_EXEC]"
$RunningPrefix = "[SCORP_EXEC_RUNNING]"
$DonePrefix = "[SCORP_EXEC_DONE]"
$BlockedPrefix = "[SCORP_EXEC_BLOCKED]"
$TrustedAuthor = "Scorp96"
New-Item -ItemType Directory -Force -Path $StateDir, $LogDir | Out-Null
$LogFile = Join-Path $LogDir "executor-v4.log"

function Write-Log {
    param([string]$Message)
    [IO.File]::AppendAllText($LogFile, "$(Get-Date -Format o) $Message$([Environment]::NewLine)", (New-Object Text.UTF8Encoding($false)))
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
}

function Write-JsonAtomic {
    param([string]$Path, $Value)
    $dir = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $id = [guid]::NewGuid().ToString("N")
    $tmp = Join-Path $dir (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), $id)
    $bak = Join-Path $dir (".{0}.{1}.bak" -f ([IO.Path]::GetFileName($Path)), $id)
    try {
        Write-Utf8NoBom -Path $tmp -Text ($Value | ConvertTo-Json -Depth 20)
        if (Test-Path -LiteralPath $Path -PathType Leaf) { [IO.File]::Replace($tmp, $Path, $bak, $true) }
        else { [IO.File]::Move($tmp, $Path) }
    } finally { Remove-Item -LiteralPath $tmp, $bak -Force -ErrorAction SilentlyContinue }
}

function Get-DefaultMutexName {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $machine = ($env:COMPUTERNAME -replace '[^A-Za-z0-9_.-]', '_')
    return "Local\ScorpGptNativeExecutorV4-$machine-$sid"
}

function Enter-Mutex {
    param([string]$Name)
    $created = $false
    $m = New-Object System.Threading.Mutex($false, $Name, [ref]$created)
    $acquired = $false
    try { $acquired = $m.WaitOne(0, $false) }
    catch [System.Threading.AbandonedMutexException] { $acquired = $true }
    return [pscustomobject]@{ Mutex = $m; Acquired = [bool]$acquired; CreatedNew = [bool]$created }
}

function Invoke-Gh {
    param([string[]]$Arguments)
    $output = & gh @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "gh failed ($code): $($output -join [Environment]::NewLine)" }
    return ($output -join [Environment]::NewLine)
}

function Convert-JsonToFlatArray {
    param([string]$Json)
    if ([string]::IsNullOrWhiteSpace($Json)) { return @() }
    $parsed = ConvertFrom-Json -InputObject $Json
    if ($null -eq $parsed) { return @() }
    $items = New-Object System.Collections.ArrayList
    function Add-FlatItem($Value) {
        if ($null -eq $Value) { return }
        if ($Value -is [System.Array]) {
            foreach ($child in $Value) { Add-FlatItem $child }
        } else { [void]$items.Add($Value) }
    }
    Add-FlatItem $parsed
    return @($items)
}

function Normalize-CommentBody {
    param([AllowNull()][string]$Body)
    if ($null -eq $Body) { return "" }
    $chars = [char[]]@([char]0xFEFF, [char]0x20, [char]0x09, [char]0x0D, [char]0x0A)
    return $Body.TrimStart($chars)
}

function Get-IssueSnapshot {
    param([int]$Number)
    return (Invoke-Gh @("issue", "view", $Number, "--repo", $Repo, "--json", "number,title,state,body,author") | ConvertFrom-Json)
}

function Get-AuthoritativeComments {
    param([int]$Number)
    $json = Invoke-Gh @("api", "--paginate", "--slurp", "/repos/$Repo/issues/$Number/comments?per_page=100")
    return Convert-JsonToFlatArray -Json $json
}

function Post-Comment {
    param([int]$Number, [string]$Text)
    $tmp = Join-Path $env:TEMP ("scorp-v4-comment-{0}-{1}.txt" -f $Number, [guid]::NewGuid().ToString("N"))
    try {
        Write-Utf8NoBom -Path $tmp -Text $Text
        Invoke-Gh @("issue", "comment", $Number, "--repo", $Repo, "--body-file", $tmp) | Out-Null
    } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

function Parse-ResultIdentity {
    param([string]$Body)
    $body = Normalize-CommentBody -Body $Body
    if (-not $body.StartsWith("SCORP_EXEC_RESULT", [StringComparison]::Ordinal)) { return $null }
    $issue = $null; $action = $null; $claim = $null; $status = $null
    if ($body -match '(?m)^Issue:\s*(\d+)\s*$') { $issue = [int]$Matches[1] }
    if ($body -match '(?m)^ActionId:\s*([^\r\n]+)\s*$') { $action = $Matches[1].Trim() }
    if ($body -match '(?m)^ClaimToken:\s*([^\r\n]+)\s*$') { $claim = $Matches[1].Trim() }
    if ($body -match '(?m)^Status:\s*([^\r\n]+)\s*$') { $status = $Matches[1].Trim() }
    if ($null -eq $issue -or [string]::IsNullOrWhiteSpace($action) -or [string]::IsNullOrWhiteSpace($claim)) { return $null }
    return [pscustomobject]@{ Issue = $issue; ActionId = $action; ClaimToken = $claim; Status = $status }
}

function Get-MatchingResults {
    param($Comments, [int]$IssueNumber, [string]$ActionId, [string]$ClaimToken)
    $matches = @()
    foreach ($comment in @($Comments)) {
        $id = Parse-ResultIdentity -Body ([string]$comment.body)
        if ($null -ne $id -and $id.Issue -eq $IssueNumber -and $id.ActionId -ceq $ActionId -and $id.ClaimToken -ceq $ClaimToken) {
            $matches += $comment
        }
    }
    return @($matches)
}

function Set-IssueTitleVerified {
    param([int]$Number, [string]$NewTitle)
    Invoke-Gh @("issue", "edit", $Number, "--repo", $Repo, "--title", $NewTitle) | Out-Null
    $after = Get-IssueSnapshot -Number $Number
    if ([string]$after.title -cne $NewTitle) { throw "title verification failed issue #$Number" }
    return $after
}

function Close-IssueVerified {
    param([int]$Number)
    $issue = Get-IssueSnapshot -Number $Number
    if ([string]$issue.state -ne "CLOSED") {
        Invoke-Gh @("issue", "close", $Number, "--repo", $Repo, "--reason", "completed") | Out-Null
        $issue = Get-IssueSnapshot -Number $Number
    }
    if ([string]$issue.state -ne "CLOSED") { throw "close verification failed issue #$Number" }
}

function Validate-Envelope {
    param($Envelope, [int]$IssueNumber)
    if ($null -eq $Envelope) { throw "envelope missing" }
    if ([string]$Envelope.protocol_version -cne $ProtocolVersion) { throw "unsupported protocol_version" }
    if ([string]::IsNullOrWhiteSpace([string]$Envelope.task_id)) { throw "task_id missing" }
    if ([string]$Envelope.action_id -notmatch '^[A-Za-z0-9._:-]{8,160}$') { throw "invalid action_id" }
    if ([string]$Envelope.action_kind -notin @("powershell", "process", "file_read", "file_write", "file_replace_exact", "git", "health")) { throw "unsupported action_kind" }
    $timeout = [int]$Envelope.timeout_seconds
    if ($timeout -lt 1 -or $timeout -gt 1800) { throw "timeout_seconds invalid" }
    if ([string]$Envelope.safety_class -notin @("standard", "approved_admin")) { throw "safety_class invalid" }
    if ([string]$Envelope.safety_class -ceq "approved_admin" -and [string]$Envelope.authorization -cne "USER_APPROVED_FULL_CONTROL") { throw "approved_admin authorization missing" }
    if ($null -eq $Envelope.payload) { throw "payload missing" }
    if ($null -ne $Envelope.issue_number -and [int]$Envelope.issue_number -ne $IssueNumber) { throw "issue_number mismatch" }
}

function Get-QueuedIssue {
    $json = Invoke-Gh @("issue", "list", "--repo", $Repo, "--state", "open", "--limit", "100", "--json", "number,title,author,body,createdAt")
    $issues = Convert-JsonToFlatArray -Json $json
    $eligible = @()
    foreach ($issue in @($issues)) {
        $number = 0
        if (-not [int]::TryParse([string]$issue.number, [ref]$number)) { continue }
        $title = [string]$issue.title
        $author = [string]$issue.author.login
        if ($title.StartsWith($QueuedPrefix, [StringComparison]::Ordinal) -and $author -ceq $TrustedAuthor) { $eligible += $issue }
    }
    return @($eligible | Sort-Object { [int]$_.number } | Select-Object -First 1)[0]
}

function Save-State { param($State); Write-JsonAtomic -Path $ActiveTaskPath -Value $State }
function Read-State {
    if (-not (Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf)) { return $null }
    return (Get-Content -LiteralPath $ActiveTaskPath -Raw | ConvertFrom-Json)
}
function Clear-State {
    Remove-Item -LiteralPath $ActiveTaskPath -Force -ErrorAction SilentlyContinue
    Write-Log "cleared active state"
}

function Test-ChildAlive {
    param($State)
    $pidValue = 0
    if (-not [int]::TryParse([string]$State.child_pid, [ref]$pidValue) -or $pidValue -le 0) { return $false }
    $p = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($null -eq $p -or $p.ProcessName -notin @("powershell", "pwsh")) { return $false }
    if (-not [string]::IsNullOrWhiteSpace([string]$State.child_started_at)) {
        try {
            $expected = [DateTimeOffset]::Parse([string]$State.child_started_at)
            $actual = [DateTimeOffset]$p.StartTime
            if ([Math]::Abs(($actual - $expected).TotalSeconds) -gt 5) { return $false }
        } catch { return $false }
    }
    return $true
}

function New-ResultReport {
    param($State, $Result)
    $resultPath = [string]$State.result_path
    $resultSha = if (Test-Path -LiteralPath $resultPath -PathType Leaf) { (Get-FileHash -LiteralPath $resultPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { "missing" }
    $tail = ""
    if ($null -ne $Result.evidence -and $null -ne $Result.evidence.combined_tail) { $tail = [string]$Result.evidence.combined_tail }
    if ($tail.Length -gt 6000) { $tail = "[tail truncated]`n" + $tail.Substring($tail.Length - 6000) }
    return @"
SCORP_EXEC_RESULT
Protocol: $ProtocolVersion
Issue: $($State.issue_number)
TaskId: $($State.task_id)
ActionId: $($State.action_id)
ClaimToken: $($State.claim_token)
Status: $($Result.status)
ExitCode: $($Result.exit_code)
StartedAt: $($Result.started_at)
FinishedAt: $($Result.finished_at)
ResultPath: $resultPath
ResultSHA256: $resultSha

$tail
"@
}

function Publish-ResultExactlyOnce {
    param($State, $Result)
    $number = [int]$State.issue_number
    $comments = Get-AuthoritativeComments -Number $number
    $matches = Get-MatchingResults -Comments $comments -IssueNumber $number -ActionId ([string]$State.action_id) -ClaimToken ([string]$State.claim_token)
    if ($matches.Count -gt 1) { throw "duplicate matching results issue #$number action=$($State.action_id)" }
    if ($matches.Count -eq 1) {
        $State.result_phase = "VERIFIED"
        Save-State $State
        return "VERIFIED_EXISTING"
    }

    if ([string]$State.result_phase -in @("POSTING", "POSTED_UNVERIFIED")) {
        for ($i = 0; $i -lt 5; $i++) {
            Start-Sleep -Milliseconds (500 * [Math]::Pow(2, $i))
            $comments = Get-AuthoritativeComments -Number $number
            $matches = Get-MatchingResults -Comments $comments -IssueNumber $number -ActionId ([string]$State.action_id) -ClaimToken ([string]$State.claim_token)
            if ($matches.Count -gt 1) { throw "duplicate matching results after ambiguous post" }
            if ($matches.Count -eq 1) {
                $State.result_phase = "VERIFIED"
                Save-State $State
                return "VERIFIED_AFTER_RESTART"
            }
        }
        throw "AMBIGUOUS_POST: prior publication phase exists but authoritative result is absent; refusing blind repost"
    }

    $State.result_phase = "POSTING"
    Save-State $State
    $report = New-ResultReport -State $State -Result $Result
    try {
        Post-Comment -Number $number -Text $report
        $State.result_phase = "POSTED_UNVERIFIED"
        Save-State $State
    } catch {
        Write-Log "post result returned error issue #$number action=$($State.action_id): $($_.Exception.Message)"
    }

    for ($i = 0; $i -lt 6; $i++) {
        Start-Sleep -Milliseconds (400 * [Math]::Pow(2, $i))
        $comments = Get-AuthoritativeComments -Number $number
        $matches = Get-MatchingResults -Comments $comments -IssueNumber $number -ActionId ([string]$State.action_id) -ClaimToken ([string]$State.claim_token)
        if ($matches.Count -gt 1) { throw "duplicate matching results after publication" }
        if ($matches.Count -eq 1) {
            $State.result_phase = "VERIFIED"
            Save-State $State
            return "VERIFIED_NEW"
        }
    }
    throw "AMBIGUOUS_POST: result could not be authoritatively verified; no repost will be attempted"
}

function Finalize-State {
    param($State)
    $number = [int]$State.issue_number
    if (-not (Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf)) {
        throw "result file missing for issue #$number"
    }
    $result = Get-Content -LiteralPath ([string]$State.result_path) -Raw | ConvertFrom-Json
    if ([string]$result.protocol_version -cne $ProtocolVersion -or [string]$result.action_id -cne [string]$State.action_id -or [string]$result.claim_token -cne [string]$State.claim_token -or [int]$result.issue_number -ne $number) {
        throw "result identity mismatch issue #$number"
    }
    Publish-ResultExactlyOnce -State $State -Result $result | Out-Null
    $doneTitle = $DonePrefix + " " + [string]$State.task_id + " " + [string]$State.action_id
    Set-IssueTitleVerified -Number $number -NewTitle $doneTitle | Out-Null
    Close-IssueVerified -Number $number
    Write-Log "completed issue #$number action=$($State.action_id) status=$($result.status)"
    Clear-State
}

function Block-AmbiguousState {
    param($State, [string]$Reason)
    $number = [int]$State.issue_number
    $title = $BlockedPrefix + " " + [string]$State.task_id + " " + [string]$State.action_id
    try { Set-IssueTitleVerified -Number $number -NewTitle $title | Out-Null } catch { Write-Log "block title update failed issue #$number: $($_.Exception.Message)" }
    Write-Log "blocked issue #$number action=$($State.action_id) reason=$Reason"
    $incident = Join-Path $StateDir ("blocked-{0}-{1}.json" -f $number, [string]$State.action_id)
    $State.block_reason = $Reason
    $State.blocked_at = (Get-Date -Format o)
    Write-JsonAtomic -Path $incident -Value $State
    Clear-State
}

function Monitor-State {
    param($State)
    $started = [DateTimeOffset]::Parse([string]$State.started_at)
    $nextHeartbeat = $started.AddSeconds($HeartbeatSeconds)
    while (Test-ChildAlive -State $State) {
        Start-Sleep -Seconds 2
        $now = [DateTimeOffset]::Now
        if ($now -ge $nextHeartbeat) {
            $elapsed = [int]($now - $started).TotalSeconds
            try { Post-Comment -Number ([int]$State.issue_number) -Text ("SCORP_EXEC_HEARTBEAT`nProtocol: $ProtocolVersion`nIssue: $($State.issue_number)`nActionId: $($State.action_id)`nClaimToken: $($State.claim_token)`nElapsedSeconds: $elapsed`nTime: $(Get-Date -Format o)") } catch { Write-Log "heartbeat failed issue #$($State.issue_number): $($_.Exception.Message)" }
            $State.last_heartbeat_at = $now.ToString("o")
            Save-State $State
            $nextHeartbeat = $now.AddSeconds($HeartbeatSeconds)
        }
        $limit = [Math]::Min([int]$State.timeout_seconds, $TaskTimeoutSeconds)
        if (($now - $started).TotalSeconds -ge $limit) {
            Write-Log "timeout issue #$($State.issue_number) pid=$($State.child_pid)"
            & taskkill.exe /PID ([int]$State.child_pid) /T /F 2>&1 | ForEach-Object { Write-Log ([string]$_) }
            Start-Sleep -Seconds 1
            if (-not (Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf)) {
                $synthetic = [ordered]@{
                    protocol_version = $ProtocolVersion; task_id = $State.task_id; issue_number = [int]$State.issue_number; claim_token = $State.claim_token; action_id = $State.action_id; action_kind = $State.action_kind; status = "TIMED_OUT"; exit_code = 124; message = "executor timeout"; started_at = $State.started_at; finished_at = (Get-Date -Format o); duration_ms = [int64](($now - $started).TotalMilliseconds); evidence = [ordered]@{}; runner_log_path = $State.runner_log_path
                }
                Write-JsonAtomic -Path ([string]$State.result_path) -Value $synthetic
            }
            break
        }
    }
    if (Test-Path -LiteralPath ([string]$State.result_path) -PathType Leaf) { Finalize-State -State $State }
    else { Block-AmbiguousState -State $State -Reason "child exited without durable result; refusing rerun" }
}

function Recover-Or-Continue {
    $state = Read-State
    if ($null -eq $state) { return $false }
    Write-Log "recovery found issue #$($state.issue_number) action=$($state.action_id) pid=$($state.child_pid) phase=$($state.result_phase)"
    if (Test-ChildAlive -State $state) { Monitor-State -State $state; return $true }
    if (Test-Path -LiteralPath ([string]$state.result_path) -PathType Leaf) {
        try { Finalize-State -State $state }
        catch {
            if ($_.Exception.Message.StartsWith("AMBIGUOUS_POST", [StringComparison]::Ordinal)) { Block-AmbiguousState -State $state -Reason $_.Exception.Message }
            else { throw }
        }
        return $true
    }
    Block-AmbiguousState -State $state -Reason "restart found no live child and no durable result; refusing rerun"
    return $true
}

function Start-QueuedIssue {
    param($Issue)
    $number = [int]$Issue.number
    $title = [string]$Issue.title
    $body = [string]$Issue.body
    $envelope = $body | ConvertFrom-Json
    Validate-Envelope -Envelope $envelope -IssueNumber $number

    $existingComments = Get-AuthoritativeComments -Number $number
    foreach ($c in @($existingComments)) {
        $id = Parse-ResultIdentity -Body ([string]$c.body)
        if ($null -ne $id -and $id.Issue -eq $number -and $id.ActionId -ceq [string]$envelope.action_id) {
            throw "issue #$number already has a result for action_id=$($envelope.action_id); refusing execution"
        }
    }

    $claim = [guid]::NewGuid().ToString("N")
    $runningTitle = $RunningPrefix + $title.Substring($QueuedPrefix.Length)
    Set-IssueTitleVerified -Number $number -NewTitle $runningTitle | Out-Null
    Post-Comment -Number $number -Text ("SCORP_EXEC_CLAIMED`nProtocol: $ProtocolVersion`nIssue: $number`nActionId: $($envelope.action_id)`nClaimToken: $claim`nHost: $env:COMPUTERNAME`nTime: $(Get-Date -Format o)")

    $envelope.issue_number = $number
    $envelope.claim_token = $claim
    $actionSafe = ([string]$envelope.action_id -replace '[^A-Za-z0-9_.-]', '_')
    $envelopePath = Join-Path $StateDir ("issue-{0}-action-{1}-envelope.json" -f $number, $actionSafe)
    $resultPath = Join-Path $StateDir ("issue-{0}-action-{1}-result.json" -f $number, $actionSafe)
    $runnerLog = Join-Path $LogDir ("issue-{0}-action-{1}-runner.log" -f $number, $actionSafe)
    Write-JsonAtomic -Path $envelopePath -Value $envelope
    Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) { throw "runner-v4.ps1 missing: $RunnerPath" }
    $started = [DateTimeOffset]::Now
    $child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $RunnerPath, "-EnvelopePath", $envelopePath, "-ResultPath", $resultPath, "-LogPath", $runnerLog) -PassThru -WindowStyle Hidden
    Start-Sleep -Milliseconds 200
    $child.Refresh()
    $state = [ordered]@{
        protocol_version = $ProtocolVersion
        issue_number = $number
        original_title = $title
        task_id = [string]$envelope.task_id
        action_id = [string]$envelope.action_id
        action_kind = [string]$envelope.action_kind
        claim_token = $claim
        timeout_seconds = [int]$envelope.timeout_seconds
        envelope_path = $envelopePath
        result_path = $resultPath
        runner_log_path = $runnerLog
        child_pid = [int]$child.Id
        child_started_at = ([DateTimeOffset]$child.StartTime).ToString("o")
        started_at = $started.ToString("o")
        last_heartbeat_at = $started.ToString("o")
        result_phase = $null
    }
    Save-State $state
    Write-Log "claimed issue #$number task=$($state.task_id) action=$($state.action_id) pid=$($state.child_pid)"
    Monitor-State -State ([pscustomobject]$state)
}

function Assert-Test {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) { throw "SELFTEST FAIL: $Name" }
    Write-Output "PASS $Name"
}

function Invoke-SelfTest {
    $flat = Convert-JsonToFlatArray -Json '[{"number":1},{"number":2}]'
    Assert-Test ($flat.Count -eq 2) "json-flat-array"
    $nested = Convert-JsonToFlatArray -Json '[[{"number":1}],[{"number":2}]]'
    Assert-Test ($nested.Count -eq 2) "json-nested-array"
    Assert-Test ((Normalize-CommentBody -Body (([char]0xFEFF) + " `t`r`nSCORP_EXEC_RESULT")) -ceq "SCORP_EXEC_RESULT") "safe-prefix-normalization"
    $good = [pscustomobject]@{ protocol_version=$ProtocolVersion; task_id="t1"; issue_number=7; action_id="action-0001"; action_kind="health"; timeout_seconds=30; safety_class="standard"; authorization=$null; payload=[pscustomobject]@{} }
    Validate-Envelope -Envelope $good -IssueNumber 7
    Assert-Test $true "valid-envelope"
    $badRejected = $false
    try { $bad = $good.PSObject.Copy(); $bad.action_kind = "model"; Validate-Envelope -Envelope $bad -IssueNumber 7 } catch { $badRejected = $true }
    Assert-Test $badRejected "unknown-action-rejected"
    $body = "SCORP_EXEC_RESULT`nProtocol: $ProtocolVersion`nIssue: 7`nActionId: action-0001`nClaimToken: claim-0001`nStatus: SUCCEEDED"
    $id = Parse-ResultIdentity -Body $body
    Assert-Test ($id.Issue -eq 7 -and $id.ActionId -ceq "action-0001" -and $id.ClaimToken -ceq "claim-0001") "result-identity-parse"
    $comments = @([pscustomobject]@{body=$body})
    Assert-Test ((Get-MatchingResults -Comments $comments -IssueNumber 7 -ActionId "action-0001" -ClaimToken "claim-0001").Count -eq 1) "matching-result"
    $dupes = @([pscustomobject]@{body=$body},[pscustomobject]@{body=$body})
    Assert-Test ((Get-MatchingResults -Comments $dupes -IssueNumber 7 -ActionId "action-0001" -ClaimToken "claim-0001").Count -eq 2) "duplicate-detection"
    $selfText = Get-Content -LiteralPath $PSCommandPath -Raw
    $runnerText = Get-Content -LiteralPath $RunnerPath -Raw
    Assert-Test (-not ($selfText -match '(?i)codex\s+exec|codex\.exe')) "executor-no-codex-invocation"
    Assert-Test (-not ($runnerText -match '(?i)codex\s+exec|codex\.exe')) "runner-no-codex-invocation"
    $tmp = Join-Path $env:TEMP ("scorp-v4-selftest-" + [guid]::NewGuid().ToString("N") + ".txt")
    try {
        Write-Utf8NoBom -Path $tmp -Text "abc"
        $bytes = [IO.File]::ReadAllBytes($tmp)
        Assert-Test (-not ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) "utf8-no-bom"
    } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    Write-Output "SELFTEST PASS"
}

if ([string]::IsNullOrWhiteSpace($MutexName)) { $MutexName = Get-DefaultMutexName }
$guard = Enter-Mutex -Name $MutexName
if (-not $guard.Acquired) {
    Write-Log "second executor instance denied mutex=$MutexName"
    if ($MutexProbe) { Write-Output "MUTEX_DENIED" }
    exit 0
}

try {
    if ($MutexProbe) { Write-Output "MUTEX_ACQUIRED"; exit 0 }
    if ($SelfTest) { Invoke-SelfTest; exit 0 }
    if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) { throw "runner-v4.ps1 missing: $RunnerPath" }
    Write-Log "executor starting repo=$Repo poll=${PollSeconds}s mutex=$MutexName"
    while ($true) {
        try {
            if (Recover-Or-Continue) { Start-Sleep -Milliseconds 200; continue }
            $issue = Get-QueuedIssue
            if ($null -eq $issue) { Start-Sleep -Seconds $PollSeconds; continue }
            Start-QueuedIssue -Issue $issue
        } catch {
            Write-Log "loop error: $($_.Exception.Message)"
            Start-Sleep -Seconds $PollSeconds
        }
    }
} finally {
    if ($guard.Acquired) { try { $guard.Mutex.ReleaseMutex() } catch {} }
    $guard.Mutex.Dispose()
}
