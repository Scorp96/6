[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$MainTaskName,
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [Parameter(Mandatory=$true)][string]$ReleaseRuntimeScript,
    [Parameter(Mandatory=$true)][string]$PythonExecutable,
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [string]$HealthPath,
    [ValidateRange(15, 3600)][int]$MaxHealthAgeSeconds = 90,
    [ValidateRange(5, 600)][int]$StartupGraceSeconds = 60
)

$ErrorActionPreference = 'Stop'

function Has-Text([string]$Haystack, [string]$Needle) {
    if ($null -eq $Haystack) { return $false }
    return $Haystack.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-RuntimeLaunchers {
    $items = @(
        Get-CimInstance Win32_Process |
        Where-Object {
            $executable = [string]$_.ExecutablePath
            $exactPython = $false
            if ($executable) {
                try {
                    $exactPython = ([IO.Path]::GetFullPath($executable) -ieq $script:python)
                } catch {
                    $exactPython = $false
                }
            }
            $exactPython -and
            (Has-Text ([string]$_.CommandLine) $script:release) -and
            (Has-Text ([string]$_.CommandLine) '--database-path') -and
            (Has-Text ([string]$_.CommandLine) $script:database) -and
            (Has-Text ([string]$_.CommandLine) '--project-id') -and
            (Has-Text ([string]$_.CommandLine) $script:project)
        }
    )
    return @($items)
}

function Read-HealthEvidence {
    $result = [ordered]@{
        valid = $false
        terminal = $false
        reason = 'HEALTH_FILE_MISSING'
        age_seconds = $null
        process_id = $null
        daemon_epoch = $null
        actor_id = $null
        status = $null
    }
    if (-not $HealthPath -or -not (Test-Path -LiteralPath $HealthPath -PathType Leaf)) {
        return [pscustomobject]$result
    }
    try {
        # Windows PowerShell coerces ISO-8601 JSON dates into local DateTime
        # values during ConvertFrom-Json. Keep the raw text so a trailing `Z`
        # remains UTC when the heartbeat age is calculated below.
        $healthRaw = Get-Content -LiteralPath $HealthPath -Raw -ErrorAction Stop
        $health = $healthRaw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        $result.reason = 'HEALTH_JSON_INVALID'
        return [pscustomobject]$result
    }
    $result.status = [string]$health.status
    if ([string]$health.protocol_version -ne 'scorp.v4.daemon-health/1') {
        $result.reason = 'HEALTH_PROTOCOL_MISMATCH'
        return [pscustomobject]$result
    }
    if ([string]$health.project_id -ne $script:project) {
        $result.reason = 'HEALTH_PROJECT_MISMATCH'
        return [pscustomobject]$result
    }
    try {
        $epoch = [int]$health.daemon_epoch
    } catch {
        $result.reason = 'HEALTH_DAEMON_EPOCH_INVALID'
        return [pscustomobject]$result
    }
    if ($epoch -lt 1) {
        $result.reason = 'HEALTH_DAEMON_EPOCH_INVALID'
        return [pscustomobject]$result
    }
    $result.daemon_epoch = $epoch
    $actor = [string]$health.actor_id
    if (-not $actor) {
        $result.reason = 'HEALTH_ACTOR_ID_MISSING'
        return [pscustomobject]$result
    }
    $result.actor_id = $actor
    try {
        $heartbeatMatch = [regex]::Match($healthRaw, '"heartbeat_at"\s*:\s*"(?<value>[^"]+)"')
        if (-not $heartbeatMatch.Success) { throw 'HEALTH_HEARTBEAT_MISSING' }
        $heartbeatText = $heartbeatMatch.Groups['value'].Value
        $styles = [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
        $heartbeat = [DateTimeOffset]::Parse(
            $heartbeatText,
            [Globalization.CultureInfo]::InvariantCulture,
            $styles
        ).ToUniversalTime()
        $age = [math]::Max(0, ([DateTimeOffset]::UtcNow - $heartbeat).TotalSeconds)
    } catch {
        $result.reason = 'HEALTH_HEARTBEAT_INVALID'
        return [pscustomobject]$result
    }
    $result.age_seconds = $age
    if ([string]$health.status -eq 'TERMINAL') {
        $result.valid = $true
        $result.terminal = $true
        $result.reason = 'TERMINAL'
        return [pscustomobject]$result
    }
    if ($age -gt $MaxHealthAgeSeconds) {
        $result.reason = 'HEALTH_STALE'
        return [pscustomobject]$result
    }
    try {
        $healthPid = [int]$health.process_id
    } catch {
        $result.reason = 'HEALTH_PROCESS_ID_INVALID'
        return [pscustomobject]$result
    }
    if ($healthPid -le 0) {
        $result.reason = 'HEALTH_PROCESS_ID_INVALID'
        return [pscustomobject]$result
    }
    $result.process_id = $healthPid
    $healthProcess = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $healthPid) -ErrorAction SilentlyContinue)
    if ($healthProcess.Count -ne 1) {
        $result.reason = 'HEALTH_PROCESS_MISSING'
        return [pscustomobject]$result
    }
    $cmd = [string]$healthProcess[0].CommandLine
    if (
        -not (Has-Text $cmd $script:release) -or
        -not (Has-Text $cmd '--database-path') -or
        -not (Has-Text $cmd $script:database) -or
        -not (Has-Text $cmd '--project-id') -or
        -not (Has-Text $cmd $script:project)
    ) {
        $result.reason = 'HEALTH_PROCESS_IDENTITY_MISMATCH'
        return [pscustomobject]$result
    }
    $result.valid = $true
    $result.reason = 'FRESH'
    return [pscustomobject]$result
}

$task = Get-ScheduledTask -TaskName $MainTaskName -ErrorAction Stop
if ($task.State -eq 'Disabled') { throw 'MAIN_RUNTIME_TASK_DISABLED' }
$taskInfo = Get-ScheduledTaskInfo -TaskName $MainTaskName -ErrorAction Stop

$script:release = [IO.Path]::GetFullPath($ReleaseRuntimeScript)
$script:python = [IO.Path]::GetFullPath($PythonExecutable)
$script:database = [IO.Path]::GetFullPath($DatabasePath)
$script:project = [string]$ProjectId

$live = @(Get-RuntimeLaunchers)
$health = Read-HealthEvidence

if ($health.terminal) {
    [pscustomobject]@{
        status='TERMINAL_NO_RESTART'
        main_task=$MainTaskName
        task_state=[string]$task.State
        health_status=[string]$health.status
        daemon_epoch=$health.daemon_epoch
        actor_id=$health.actor_id
    }
    exit 0
}

if ($live.Count -eq 1 -and $health.valid) {
    [pscustomobject]@{
        status='HEALTHY_RUNNING'
        main_task=$MainTaskName
        task_state=[string]$task.State
        live_processes=$live.Count
        health_age_seconds=$health.age_seconds
        health_process_id=$health.process_id
        daemon_epoch=$health.daemon_epoch
        actor_id=$health.actor_id
    }
    exit 0
}

$lastRunAgeSeconds = [double]::PositiveInfinity
try {
    if ($taskInfo.LastRunTime -and $taskInfo.LastRunTime -gt [DateTime]::MinValue) {
        $lastRunAgeSeconds = [math]::Max(0, ((Get-Date) - $taskInfo.LastRunTime).TotalSeconds)
    }
} catch {
    $lastRunAgeSeconds = [double]::PositiveInfinity
}
if ($task.State -eq 'Running' -and $live.Count -ge 1 -and $lastRunAgeSeconds -le $StartupGraceSeconds) {
    [pscustomobject]@{
        status='STARTING'
        main_task=$MainTaskName
        task_state=[string]$task.State
        live_processes=$live.Count
        health_reason=[string]$health.reason
        health_age_seconds=$health.age_seconds
        startup_age_seconds=$lastRunAgeSeconds
    }
    exit 0
}

$priorState = [string]$task.State
$recoveryReason = if ($live.Count -eq 0) { 'RUNTIME_PROCESS_ABSENT' } else { [string]$health.reason }
if ($task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $MainTaskName -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 500
        $live = @(Get-RuntimeLaunchers)
        if ($live.Count -eq 0) { break }
    } while ((Get-Date) -lt $deadline)
    if ($live.Count -ne 0) { throw 'MAIN_RUNTIME_STOP_INCOMPLETE' }
}
Start-ScheduledTask -TaskName $MainTaskName -ErrorAction Stop
[pscustomobject]@{
    status='RECOVERY_START_REQUESTED'
    main_task=$MainTaskName
    prior_task_state=$priorState
    live_processes=$live.Count
    recovery_reason=$recoveryReason
    health_age_seconds=$health.age_seconds
}
