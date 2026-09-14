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
$AgentRoot = "C:\ScorpAgent"
$LogDir = Join-Path $AgentRoot "logs"
$StateDir = Join-Path $AgentRoot "state"
$ActiveTaskPath = Join-Path $StateDir "active-task.json"
$QueuedPrefix = "[SCORP_AGENT]"
$RunningPrefix = "[SCORP_RUNNING]"
$TrustedAuthor = "Scorp96"
New-Item -ItemType Directory -Force -Path $LogDir, $StateDir | Out-Null
$LogFile = Join-Path $LogDir "relay.log"

function Write-RelayLog {
    param([string]$Message)
    Add-Content -LiteralPath $LogFile -Value "$(Get-Date -Format o) $Message" -Encoding UTF8
}

function Get-DefaultMutexName {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $machine = ($env:COMPUTERNAME -replace '[^A-Za-z0-9_.-]', '_')
    return "Local\ScorpComputerAgentRelay-v3-$machine-$sid"
}

function Enter-RelayMutex {
    param([string]$Name)
    $createdNew = $false
    $mutex = New-Object System.Threading.Mutex($false, $Name, [ref]$createdNew)
    $acquired = $false
    try { $acquired = $mutex.WaitOne(0, $false) }
    catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
        Write-RelayLog "acquired abandoned relay mutex name=$Name"
    }
    return [pscustomobject]@{ Mutex = $mutex; Acquired = [bool]$acquired; CreatedNew = [bool]$createdNew }
}

function Invoke-Gh {
    param([string[]]$Arguments)
    $output = & gh @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "gh failed ($code): $($output -join [Environment]::NewLine)" }
    return ($output -join [Environment]::NewLine)
}

function Get-IssueSnapshot {
    param([int]$Number)
    $json = Invoke-Gh @("issue", "view", $Number, "--repo", $Repo, "--json", "number,title,state,comments")
    return ($json | ConvertFrom-Json)
}

function Post-IssueComment {
    param([int]$Number, [string]$Text)
    $tmp = Join-Path $env:TEMP ("scorp-comment-{0}-{1}.txt" -f $Number, [guid]::NewGuid())
    Set-Content -LiteralPath $tmp -Value $Text -Encoding UTF8
    try { Invoke-Gh @("issue", "comment", $Number, "--repo", $Repo, "--body-file", $tmp) | Out-Null }
    finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

function Test-CommentMarker {
    param($Comments, [string]$Marker)
    foreach ($comment in @($Comments)) {
        $body = [string]$comment.body
        if ($body.TrimStart([char]0xFEFF).StartsWith($Marker, [StringComparison]::Ordinal)) { return $true }
    }
    return $false
}

function Test-ResultCommentExists {
    param($Issue)
    return (Test-CommentMarker -Comments $Issue.comments -Marker "SCORP_AGENT_RESULT")
}

function ConvertTo-RunningTitle {
    param([string]$Title)
    if ([string]::IsNullOrWhiteSpace($Title) -or -not $Title.StartsWith($QueuedPrefix, [StringComparison]::Ordinal)) {
        throw "title is not queued: $Title"
    }
    return $RunningPrefix + $Title.Substring($QueuedPrefix.Length)
}

function ConvertTo-TerminalTitle {
    param([string]$OriginalTitle, [bool]$TimedOut)
    $prefix = if ($TimedOut) { "[SCORP_TIMEOUT]" } else { "[SCORP_FAILED]" }
    if ($OriginalTitle.StartsWith($QueuedPrefix, [StringComparison]::Ordinal)) {
        return $prefix + $OriginalTitle.Substring($QueuedPrefix.Length)
    }
    return "$prefix $OriginalTitle"
}

function Set-IssueTitleVerified {
    param([int]$Number, [string]$NewTitle, [string]$ExpectedCurrentTitle = "")
    if (-not [string]::IsNullOrEmpty($ExpectedCurrentTitle)) {
        $before = Get-IssueSnapshot -Number $Number
        if ([string]$before.state -ne "OPEN" -or [string]$before.title -cne $ExpectedCurrentTitle) {
            throw "issue #$Number changed before title transition; expected='$ExpectedCurrentTitle' actual='$($before.title)' state=$($before.state)"
        }
    }
    Invoke-Gh @("issue", "edit", $Number, "--repo", $Repo, "--title", $NewTitle) | Out-Null
    $after = Get-IssueSnapshot -Number $Number
    if ([string]$after.title -cne $NewTitle) {
        throw "issue #$Number title verification failed; expected='$NewTitle' actual='$($after.title)'"
    }
    return $after
}

function Write-JsonAtomic {
    param([string]$Path, $Value)
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $operationId = [guid]::NewGuid().ToString("N")
    $tmp = Join-Path $directory (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), $operationId)
    $backup = Join-Path $directory (".{0}.{1}.bak" -f ([IO.Path]::GetFileName($Path)), $operationId)
    try {
        $json = $Value | ConvertTo-Json -Depth 10
        [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $Path -PathType Leaf) { [IO.File]::Replace($tmp, $Path, $backup, $true) }
        else { [IO.File]::Move($tmp, $Path) }
    } finally { Remove-Item -LiteralPath $tmp, $backup -Force -ErrorAction SilentlyContinue }
}

function Write-TextAtomic {
    param([string]$Path, [string]$Value)
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $operationId = [guid]::NewGuid().ToString("N")
    $tmp = Join-Path $directory (".{0}.{1}.tmp" -f ([IO.Path]::GetFileName($Path)), $operationId)
    $backup = Join-Path $directory (".{0}.{1}.bak" -f ([IO.Path]::GetFileName($Path)), $operationId)
    try {
        [IO.File]::WriteAllText($tmp, $Value, [Text.Encoding]::ASCII)
        if (Test-Path -LiteralPath $Path -PathType Leaf) { [IO.File]::Replace($tmp, $Path, $backup, $true) }
        else { [IO.File]::Move($tmp, $Path) }
    } finally { Remove-Item -LiteralPath $tmp, $backup -Force -ErrorAction SilentlyContinue }
}

function Read-ActiveTask {
    if (-not (Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf)) { return $null }
    try { $state = Get-Content -LiteralPath $ActiveTaskPath -Raw | ConvertFrom-Json }
    catch { throw "active task state is not valid JSON: $($_.Exception.Message)" }
    if ($null -eq $state.issueNumber -or [string]::IsNullOrWhiteSpace([string]$state.originalTitle)) {
        throw "active task state is missing issueNumber/originalTitle"
    }
    return $state
}

function Save-ActiveTask { param($State); Write-JsonAtomic -Path $ActiveTaskPath -Value $State }
function Clear-ActiveTask {
    Remove-Item -LiteralPath $ActiveTaskPath -Force -ErrorAction Stop
    Write-RelayLog "cleared active task state"
}

function Test-RecordedChildAlive {
    param($State)
    $pidValue = 0
    if (-not [int]::TryParse([string]$State.childPid, [ref]$pidValue) -or $pidValue -le 0) { return $false }
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.ProcessName -notin @("powershell", "pwsh")) { return $false }
    if (-not [string]::IsNullOrWhiteSpace([string]$State.childStartedAt)) {
        try {
            $expected = [DateTimeOffset]::Parse([string]$State.childStartedAt)
            $actual = [DateTimeOffset]$process.StartTime
            if ([Math]::Abs(($actual - $expected).TotalSeconds) -gt 5) {
                Write-RelayLog "pid reuse rejected issue #$($State.issueNumber) pid=$pidValue"
                return $false
            }
        } catch {
            Write-RelayLog "child identity check failed issue #$($State.issueNumber) pid=$pidValue error=$($_.Exception.Message)"
            return $false
        }
    }
    return $true
}

function Get-RecoveryDecision {
    param([bool]$ChildAlive, [bool]$HasOutput, [bool]$HasExit, [bool]$HasResult)
    if ($ChildAlive) { return "ADOPT" }
    if ($HasOutput -and $HasExit) { return "FINALIZE" }
    if ($HasResult) { return "FINALIZE_REMOTE" }
    return "REQUEUE"
}

function Test-StateFileExists {
    param($PathValue)
    $path = [string]$PathValue
    if ([string]::IsNullOrWhiteSpace($path)) { return $false }
    return (Test-Path -LiteralPath $path -PathType Leaf -ErrorAction SilentlyContinue)
}

function Get-ExitCodeFromResultComment {
    param($Issue)
    foreach ($comment in @($Issue.comments)) {
        $body = ([string]$comment.body).TrimStart([char]0xFEFF)
        if ($body.StartsWith("SCORP_AGENT_RESULT", [StringComparison]::Ordinal) -and $body -match '(?m)^ExitCode:\s*(-?\d+)\s*$') {
            return [int]$Matches[1]
        }
    }
    return $null
}

function Complete-ActiveTask {
    param($State)
    $number = [int]$State.issueNumber
    $issue = Get-IssueSnapshot -Number $number
    $hasResult = Test-ResultCommentExists -Issue $issue
    $exitCode = $null
    if ($null -ne $State.exitCode -and [string]$State.exitCode -match '^-?\d+$') {
        $exitCode = [int]$State.exitCode
    } elseif (Test-StateFileExists -PathValue $State.exitFile) {
        $rawExit = (Get-Content -LiteralPath ([string]$State.exitFile) -Raw -ErrorAction Stop).Trim()
        if ($rawExit -notmatch '^-?\d+$') { throw "issue #$number exit file is invalid" }
        $exitCode = [int]$rawExit
        $State.exitCode = $exitCode
        Save-ActiveTask -State $State
    } elseif ($hasResult) {
        $exitCode = Get-ExitCodeFromResultComment -Issue $issue
        if ($null -eq $exitCode) { throw "issue #$number has a result marker but no parseable ExitCode" }
        $State.exitCode = $exitCode
        Save-ActiveTask -State $State
    } else { throw "issue #$number cannot finalize without an exit code" }

    $timedOut = [bool]$State.timedOut -or $exitCode -eq 124
    if (-not $hasResult) {
        if (-not (Test-StateFileExists -PathValue $State.outputFile)) {
            throw "issue #$number cannot post result because output file is missing"
        }
        $result = Get-Content -LiteralPath ([string]$State.outputFile) -Raw -ErrorAction SilentlyContinue
        if ([string]::IsNullOrEmpty($result)) { $result = "(no output captured)" }
        if ($result.Length -gt 50000) { $result = "[truncated to last 50000 chars]`n" + $result.Substring($result.Length - 50000) }
        $timeoutLine = if ($timedOut) { "`nTimedOut: true`nTimeoutSeconds: $TaskTimeoutSeconds" } else { "" }
        $report = "SCORP_AGENT_RESULT`nExitCode: $exitCode$timeoutLine`nHost: $env:COMPUTERNAME`nTime: $(Get-Date -Format o)`n`n$result"
        Post-IssueComment -Number $number -Text $report
        $issue = Get-IssueSnapshot -Number $number
        if (-not (Test-ResultCommentExists -Issue $issue)) { throw "issue #$number result comment could not be verified" }
        Write-RelayLog "posted result issue #$number exit=$exitCode"
    } else { Write-RelayLog "result already exists issue #$number; duplicate suppressed" }

    $issue = Get-IssueSnapshot -Number $number
    if ($exitCode -eq 0) {
        if ([string]$issue.state -ne "CLOSED") {
            Invoke-Gh @("issue", "close", $number, "--repo", $Repo, "--reason", "completed") | Out-Null
            $issue = Get-IssueSnapshot -Number $number
        }
        if ([string]$issue.state -ne "CLOSED") { throw "issue #$number close could not be verified" }
        Write-RelayLog "completed issue #$number"
    } else {
        $terminalTitle = ConvertTo-TerminalTitle -OriginalTitle ([string]$State.originalTitle) -TimedOut $timedOut
        if ([string]$issue.title -cne $terminalTitle) { $issue = Set-IssueTitleVerified -Number $number -NewTitle $terminalTitle }
        if ([string]$issue.title -cne $terminalTitle) { throw "issue #$number terminal title could not be verified" }
        if ($timedOut) { Write-RelayLog "timed out issue #$number" }
        else { Write-RelayLog "failed issue #$number exit=$exitCode" }
    }
    Clear-ActiveTask
}

function Wait-ActiveTask {
    param($State)
    $number = [int]$State.issueNumber
    $started = [DateTimeOffset]::Parse([string]$State.startedAt)
    $lastHeartbeat = $started
    if (-not [string]::IsNullOrWhiteSpace([string]$State.lastHeartbeatAt)) {
        $lastHeartbeat = [DateTimeOffset]::Parse([string]$State.lastHeartbeatAt)
    }
    $nextHeartbeat = $lastHeartbeat.AddSeconds($HeartbeatSeconds)
    Write-RelayLog "monitor issue #$number pid=$($State.childPid) timeout=${TaskTimeoutSeconds}s"
    while (Test-RecordedChildAlive -State $State) {
        Start-Sleep -Seconds 2
        $now = [DateTimeOffset]::Now
        if ($now -ge $nextHeartbeat) {
            $elapsed = [int]($now - $started).TotalSeconds
            Post-IssueComment -Number $number -Text ("SCORP_AGENT_HEARTBEAT`nHost: $env:COMPUTERNAME`nElapsedSeconds: $elapsed`nTime: $(Get-Date -Format o)")
            $State.lastHeartbeatAt = $now.ToString("o")
            Save-ActiveTask -State $State
            Write-RelayLog "heartbeat issue #$number elapsed=${elapsed}s"
            $nextHeartbeat = $now.AddSeconds($HeartbeatSeconds)
        }
        if (($now - $started).TotalSeconds -ge $TaskTimeoutSeconds) {
            $State.timedOut = $true
            $State.exitCode = 124
            Save-ActiveTask -State $State
            Write-TextAtomic -Path ([string]$State.exitFile) -Value "124`r`n"
            Write-RelayLog "timeout issue #$number pid=$($State.childPid)"
            & taskkill.exe /PID ([int]$State.childPid) /T /F 2>&1 | Add-Content -LiteralPath $LogFile -Encoding UTF8
            break
        }
    }
    Start-Sleep -Milliseconds 500
    $fresh = Read-ActiveTask
    if ($null -eq $fresh) { throw "active task state disappeared while monitoring issue #$number" }
    Complete-ActiveTask -State $fresh
}

function Invoke-StaleRecovery {
    param($State, $Issue)
    $number = [int]$State.issueNumber
    if (Test-ResultCommentExists -Issue $Issue) { Complete-ActiveTask -State $State; return }
    if (-not (Test-CommentMarker -Comments $Issue.comments -Marker "SCORP_AGENT_RECOVERY")) {
        $diagnostic = "SCORP_AGENT_RECOVERY`nHost: $env:COMPUTERNAME`nTime: $(Get-Date -Format o)`nDecision: stale state; no recorded child is alive and required exit/output evidence is incomplete. Requeueing without execution."
        Post-IssueComment -Number $number -Text $diagnostic
        $Issue = Get-IssueSnapshot -Number $number
        Write-RelayLog "posted stale recovery diagnostic issue #$number"
    }
    if (Test-ResultCommentExists -Issue $Issue) { Complete-ActiveTask -State $State; return }
    $requeueTitle = [string]$State.originalTitle
    if (-not $requeueTitle.StartsWith($QueuedPrefix, [StringComparison]::Ordinal)) {
        throw "issue #$number cannot be safely requeued because original title is invalid"
    }
    if ([string]$Issue.state -eq "CLOSED") { Invoke-Gh @("issue", "reopen", $number, "--repo", $Repo) | Out-Null }
    $Issue = Get-IssueSnapshot -Number $number
    if (Test-ResultCommentExists -Issue $Issue) { Complete-ActiveTask -State $State; return }
    if ([string]$Issue.title -cne $requeueTitle) { $Issue = Set-IssueTitleVerified -Number $number -NewTitle $requeueTitle }
    if ([string]$Issue.title -cne $requeueTitle -or [string]$Issue.state -ne "OPEN") { throw "issue #$number requeue could not be verified" }
    $Issue = Get-IssueSnapshot -Number $number
    if (Test-ResultCommentExists -Issue $Issue) { Complete-ActiveTask -State $State; return }
    Clear-ActiveTask
    Write-RelayLog "requeued stale issue #$number without execution"
}

function Resolve-ActiveTask {
    $state = Read-ActiveTask
    if ($null -eq $state) { return }
    $number = [int]$state.issueNumber
    $issue = Get-IssueSnapshot -Number $number
    $childAlive = Test-RecordedChildAlive -State $state
    $hasOutput = Test-StateFileExists -PathValue $state.outputFile
    $hasExit = Test-StateFileExists -PathValue $state.exitFile
    $hasResult = Test-ResultCommentExists -Issue $issue
    $decision = Get-RecoveryDecision -ChildAlive $childAlive -HasOutput $hasOutput -HasExit $hasExit -HasResult $hasResult
    Write-RelayLog "recovery issue #$number decision=$decision childAlive=$childAlive output=$hasOutput exit=$hasExit result=$hasResult"
    switch ($decision) {
        "ADOPT" { Write-RelayLog "adopting active child issue #$number pid=$($state.childPid)"; Wait-ActiveTask -State $state }
        "FINALIZE" { Complete-ActiveTask -State $state }
        "FINALIZE_REMOTE" { Complete-ActiveTask -State $state }
        "REQUEUE" { Invoke-StaleRecovery -State $state -Issue $issue }
        default { throw "unknown recovery decision: $decision" }
    }
}

function Start-ClaimedTask {
    param($Issue)
    $number = [int]$Issue.number
    $originalTitle = [string]$Issue.title
    $runningTitle = ConvertTo-RunningTitle -Title $originalTitle
    $current = Get-IssueSnapshot -Number $number
    if (Test-ResultCommentExists -Issue $current) { throw "issue #$number already has SCORP_AGENT_RESULT; refusing execution" }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $token = [guid]::NewGuid().ToString("N")
    $outFile = Join-Path $LogDir ("issue-{0}-{1}.log" -f $number, $stamp)
    $exitFile = Join-Path $StateDir ("issue-{0}-{1}-exit.txt" -f $number, $stamp)
    $gateFile = Join-Path $StateDir ("issue-{0}-{1}.start" -f $number, $token)
    [IO.File]::WriteAllText($outFile, "", (New-Object Text.UTF8Encoding($false)))
    Remove-Item -LiteralPath $exitFile, $gateFile -Force -ErrorAction SilentlyContinue
    $state = [pscustomobject][ordered]@{
        schemaVersion = 3; issueNumber = $number; originalTitle = $originalTitle; runningTitle = $runningTitle
        startedAt = [DateTimeOffset]::Now.ToString("o"); outputFile = $outFile; exitFile = $exitFile
        childPid = 0; childStartedAt = $null; lastHeartbeatAt = $null; timedOut = $false
        exitCode = $null; claimToken = $token
    }
    Save-ActiveTask -State $state

    # Persist the recovery intent before changing the remote title. If the relay
    # stops between these operations, stale recovery can safely restore the
    # queued title; Codex is never launched until the running title is verified.
    Set-IssueTitleVerified -Number $number -NewTitle $runningTitle -ExpectedCurrentTitle $originalTitle | Out-Null
    Write-RelayLog "claim transition verified issue #$number '$originalTitle' -> '$runningTitle'"
    Post-IssueComment -Number $number -Text ("SCORP_AGENT_CLAIMED`nHost: $env:COMPUTERNAME`nUser: $env:USERNAME`nClaimToken: $token`nTime: $(Get-Date -Format o)")

    $prompt = @"
You are the local execution engine for Scorp Computer Agent on this Windows PC.
Execute the user's task on THIS computer. Use terminal/filesystem/system capabilities directly.
Do not merely explain commands: perform the task and verify the result.
If the task cannot be completed, diagnose the blocker and state the exact next action.
At the end, print a concise execution report with: STATUS, ACTIONS, VERIFICATION, REMAINING_BLOCKERS.

GitHub task issue #${number}:
Title: $originalTitle
Body:
$($Issue.body)
"@
    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codex -or -not [IO.Path]::IsPathRooted([string]$codex.Source)) { throw "Codex CLI absolute path not found" }
    $codexPath = [string]$codex.Source
    $promptB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($prompt))
    $childScript = @"
`$ErrorActionPreference = 'Continue'
`$gateDeadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
while (-not (Test-Path -LiteralPath '$gateFile' -PathType Leaf) -and [DateTimeOffset]::UtcNow -lt `$gateDeadline) { Start-Sleep -Milliseconds 100 }
if (-not (Test-Path -LiteralPath '$gateFile' -PathType Leaf)) {
    Set-Content -LiteralPath '$outFile' -Value '(launch gate was not released; Codex was not executed)' -Encoding UTF8
    Set-Content -LiteralPath '$exitFile' -Value '125' -Encoding ASCII
    exit 125
}
Remove-Item -LiteralPath '$gateFile' -Force -ErrorAction SilentlyContinue
`$prompt = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$promptB64'))
& '$codexPath' exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check `$prompt 2>&1 | Tee-Object -FilePath '$outFile'
`$code = `$LASTEXITCODE
Set-Content -LiteralPath '$exitFile' -Value `$code -Encoding ASCII
exit `$code
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childScript))
    $child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -PassThru -WindowStyle Hidden
    $state.childPid = [int]$child.Id
    $state.childStartedAt = ([DateTimeOffset]$child.StartTime).ToString("o")
    Save-ActiveTask -State $state
    [IO.File]::WriteAllText($gateFile, $token, [Text.Encoding]::ASCII)
    Write-RelayLog "issue #$number child pid=$($child.Id) gate released timeout=${TaskTimeoutSeconds}s"
}

function Get-RelayTypeName {
    param($Value)
    if ($null -eq $Value) { return "<null>" }
    return $Value.GetType().FullName
}

function Write-IssueParseDiagnostic {
    param([string]$Reason, $Issue, $NumberValue)
    $issueType = Get-RelayTypeName -Value $Issue
    $numberType = Get-RelayTypeName -Value $NumberValue
    Write-RelayLog "issue parse failure reason=$Reason issueType=$issueType numberType=$numberType"
}

function ConvertFrom-IssueListJson {
    param([string]$Json)
    try { $parsedIssues = ConvertFrom-Json -InputObject $Json }
    catch {
        Write-IssueParseDiagnostic -Reason ("invalid-json: " + $_.Exception.Message) -Issue $null -NumberValue $null
        throw
    }

    $issues = New-Object System.Collections.ArrayList
    if ($null -eq $parsedIssues) {
        # An empty JSON root array is returned as null by Windows PowerShell 5.1.
    } elseif ($parsedIssues -is [System.Array]) {
        for ($index = 0; $index -lt $parsedIssues.Count; $index++) {
            [void]$issues.Add($parsedIssues[$index])
        }
    } else {
        [void]$issues.Add($parsedIssues)
    }

    foreach ($candidate in $issues) {
        if ($candidate -is [System.Array]) {
            Write-IssueParseDiagnostic -Reason "nested-array" -Issue $candidate -NumberValue $null
            throw "GitHub issue list contains a nested array"
        }
        if ($null -eq $candidate -or $candidate -isnot [pscustomobject]) {
            Write-IssueParseDiagnostic -Reason "non-object-entry" -Issue $candidate -NumberValue $null
            throw "GitHub issue list contains a non-PSCustomObject entry"
        }
        Write-Output $candidate
    }
}

function Select-QueuedIssueFromJson {
    param([string]$Json)
    $issues = @(ConvertFrom-IssueListJson -Json $Json)
    $eligible = @($issues | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_.title) -and
        ([string]$_.title).StartsWith($QueuedPrefix, [StringComparison]::Ordinal) -and
        [string]$_.author.login -eq $TrustedAuthor
    } | Sort-Object createdAt)
    if ($eligible.Count -eq 0) { return $null }

    $selected = @($eligible | Select-Object -First 1)
    if ($selected.Count -ne 1) {
        $diagnosticIssue = if ($selected.Count -gt 0) { $selected[0] } else { $null }
        $diagnosticNumber = if ($null -ne $diagnosticIssue) { $diagnosticIssue.number } else { $null }
        Write-IssueParseDiagnostic -Reason "selected-count-$($selected.Count)" -Issue $diagnosticIssue -NumberValue $diagnosticNumber
        throw "GitHub issue selection did not produce exactly one object"
    }

    $issue = $selected[0]
    $numberValue = $issue.number
    $numberItems = @($numberValue)
    if ($issue -isnot [pscustomobject] -or $numberItems.Count -ne 1 -or $numberValue -is [System.Array]) {
        Write-IssueParseDiagnostic -Reason "number-not-one-scalar" -Issue $issue -NumberValue $numberValue
        throw "selected GitHub issue must be one PSCustomObject with one scalar number"
    }

    $numericTypeCodes = @(
        [TypeCode]::Byte, [TypeCode]::SByte, [TypeCode]::Int16, [TypeCode]::UInt16,
        [TypeCode]::Int32, [TypeCode]::UInt32, [TypeCode]::Int64, [TypeCode]::UInt64
    )
    $numberTypeCode = [Type]::GetTypeCode($numberValue.GetType())
    $number = 0
    $numberText = [Convert]::ToString($numberValue, [Globalization.CultureInfo]::InvariantCulture)
    $numberIsInt = $numericTypeCodes -contains $numberTypeCode -and [int]::TryParse(
        $numberText,
        [Globalization.NumberStyles]::Integer,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$number
    )
    if (-not $numberIsInt -or $number -le 0) {
        Write-IssueParseDiagnostic -Reason "number-not-positive-int32" -Issue $issue -NumberValue $numberValue
        throw "selected GitHub issue number is not a positive Int32 scalar"
    }

    $issue.number = [int]$number
    if (@($issue.number).Count -ne 1 -or $issue.number -isnot [int]) {
        Write-IssueParseDiagnostic -Reason "number-normalization-failed" -Issue $issue -NumberValue $issue.number
        throw "selected GitHub issue number did not normalize to one Int32"
    }
    return $issue
}

function Get-QueuedIssue {
    $json = Invoke-Gh @("issue", "list", "--repo", $Repo, "--state", "open", "--limit", "100", "--json", "number,title,body,author,createdAt")
    return (Select-QueuedIssueFromJson -Json $json)
}

function Assert-SelfTest {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) { throw "self-test failed: $Name" }
    Write-Output "PASS $Name"
}

function Invoke-RelaySelfTest {
    $running = ConvertTo-RunningTitle -Title "[SCORP_AGENT] bootstrap"
    Assert-SelfTest -Condition ($running -ceq "[SCORP_RUNNING] bootstrap") -Name "queued-to-running-transition"

    $emptySelection = Select-QueuedIssueFromJson -Json '[]'
    Assert-SelfTest -Condition ($null -eq $emptySelection) -Name "issue-json-empty-array"

    $singleSelection = Select-QueuedIssueFromJson -Json '[{"number":41,"title":"[SCORP_AGENT] one","body":"","author":{"login":"Scorp96"},"createdAt":"2026-09-09T00:00:00Z"}]'
    Assert-SelfTest -Condition ($singleSelection -is [pscustomobject] -and @($singleSelection.number).Count -eq 1 -and $singleSelection.number -is [int] -and $singleSelection.number -eq 41) -Name "issue-json-single-array-scalar-int"

    $manySelection = Select-QueuedIssueFromJson -Json '[{"number":52,"title":"[SCORP_AGENT] later","body":"","author":{"login":"Scorp96"},"createdAt":"2026-09-09T02:00:00Z"},{"number":51,"title":"[SCORP_AGENT] earlier","body":"","author":{"login":"Scorp96"},"createdAt":"2026-09-09T01:00:00Z"},{"number":50,"title":"ordinary","body":"","author":{"login":"Scorp96"},"createdAt":"2026-09-09T00:00:00Z"}]'
    Assert-SelfTest -Condition ($manySelection -is [pscustomobject] -and @($manySelection.number).Count -eq 1 -and $manySelection.number -is [int] -and $manySelection.number -eq 51) -Name "issue-json-many-array-selects-one-scalar-int"

    $nestedRejected = $false
    try { Select-QueuedIssueFromJson -Json '[[{"number":61,"title":"[SCORP_AGENT] nested","author":{"login":"Scorp96"},"createdAt":"2026-09-09T00:00:00Z"}]]' | Out-Null }
    catch { $nestedRejected = $true }
    Assert-SelfTest -Condition $nestedRejected -Name "issue-json-nested-array-rejected"

    $arrayNumberRejected = $false
    try { Select-QueuedIssueFromJson -Json '[{"number":[71,72],"title":"[SCORP_AGENT] array-number","author":{"login":"Scorp96"},"createdAt":"2026-09-09T00:00:00Z"}]' | Out-Null }
    catch { $arrayNumberRejected = $true }
    Assert-SelfTest -Condition $arrayNumberRejected -Name "issue-json-array-number-rejected"

    $testDir = Join-Path $env:TEMP ("scorp-relay-selftest-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $testDir -Force | Out-Null
    try {
        $testStatePath = Join-Path $testDir "active-task.json"
        $testState = [pscustomobject][ordered]@{
            issueNumber = 999999; originalTitle = "[SCORP_AGENT] simulation"; startedAt = [DateTimeOffset]::Now.ToString("o")
            outputFile = (Join-Path $testDir "output.log"); exitFile = (Join-Path $testDir "exit.txt"); childPid = 43210
        }
        Write-JsonAtomic -Path $testStatePath -Value $testState
        $testState.childPid = 43211
        Write-JsonAtomic -Path $testStatePath -Value $testState
        $testExitPath = Join-Path $testDir "atomic-exit.txt"
        Write-TextAtomic -Path $testExitPath -Value "1"
        Write-TextAtomic -Path $testExitPath -Value "124"
        $roundTrip = Get-Content -LiteralPath $testStatePath -Raw | ConvertFrom-Json
        $roundTripExit = Get-Content -LiteralPath $testExitPath -Raw
        Assert-SelfTest -Condition ([int]$roundTrip.issueNumber -eq 999999 -and [string]$roundTrip.originalTitle -ceq "[SCORP_AGENT] simulation" -and [int]$roundTrip.childPid -eq 43211 -and $roundTripExit -ceq "124") -Name "active-state-serialization-and-replacement"
    } finally { Remove-Item -LiteralPath $testDir -Recurse -Force -ErrorAction SilentlyContinue }
    $decision = Get-RecoveryDecision -ChildAlive $false -HasOutput $false -HasExit $false -HasResult $false
    Assert-SelfTest -Condition ($decision -ceq "REQUEUE") -Name "stale-recovery-decision"
    $mockIssue = [pscustomobject]@{ comments = @([pscustomobject]@{ body = "SCORP_AGENT_RESULT`nExitCode: 0" }) }
    Assert-SelfTest -Condition (Test-ResultCommentExists -Issue $mockIssue) -Name "duplicate-result-prevention"
    $probeOutput = & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $PSCommandPath -MutexProbe -MutexName $MutexName 2>&1
    $probeCode = $LASTEXITCODE
    $probeText = $probeOutput -join [Environment]::NewLine
    Assert-SelfTest -Condition ($probeCode -eq 0 -and $probeText -match 'SINGLE_INSTANCE_DENIED') -Name "second-relay-instance-exits"
    Write-Output "SELF_TEST_RESULT: PASS (10/10)"
}

if ([string]::IsNullOrWhiteSpace($MutexName)) { $MutexName = Get-DefaultMutexName }
$mutexHandle = Enter-RelayMutex -Name $MutexName
if (-not $mutexHandle.Acquired) {
    Write-RelayLog "second relay instance denied mutex=$MutexName; exiting cleanly"
    Write-Output "SINGLE_INSTANCE_DENIED: mutex=$MutexName"
    $mutexHandle.Mutex.Dispose()
    exit 0
}

try {
    if ($MutexProbe) { Write-Output "MUTEX_PROBE_UNEXPECTEDLY_ACQUIRED: mutex=$MutexName"; exit 2 }
    if ($SelfTest) { Invoke-RelaySelfTest; exit 0 }
    Write-RelayLog "relay starting repo=$Repo poll=${PollSeconds}s timeout=${TaskTimeoutSeconds}s heartbeat=${HeartbeatSeconds}s mutex=$MutexName"
    while ($true) {
        try {
            if (Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf) { Resolve-ActiveTask }
            else {
                $issue = Get-QueuedIssue
                if ($null -ne $issue) { Start-ClaimedTask -Issue $issue; Resolve-ActiveTask }
            }
        } catch { Write-RelayLog ("loop error: " + $_.Exception.Message) }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    if ($mutexHandle.Acquired) { try { $mutexHandle.Mutex.ReleaseMutex() } catch {} }
    $mutexHandle.Mutex.Dispose()
}
