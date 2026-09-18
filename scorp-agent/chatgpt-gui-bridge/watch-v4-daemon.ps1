[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$MainTaskName,
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [Parameter(Mandatory=$true)][string]$ReleaseRuntimeScript,
    [Parameter(Mandatory=$true)][string]$PythonExecutable,
    [string]$HealthPath
)

$ErrorActionPreference = 'Stop'

function Has-Text([string]$Haystack, [string]$Needle) {
    if ($null -eq $Haystack) { return $false }
    return $Haystack.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

$task = Get-ScheduledTask -TaskName $MainTaskName -ErrorAction Stop
if ($task.State -eq 'Disabled') { throw 'MAIN_RUNTIME_TASK_DISABLED' }

$release = [IO.Path]::GetFullPath($ReleaseRuntimeScript)
$python = [IO.Path]::GetFullPath($PythonExecutable)
$live = @(
    Get-CimInstance Win32_Process |
    Where-Object {
        $executable = [string]$_.ExecutablePath
        $exactPython = $false
        if ($executable) {
            try {
                $exactPython = ([IO.Path]::GetFullPath($executable) -ieq $python)
            } catch {
                $exactPython = $false
            }
        }
        $exactPython -and
        (Has-Text ([string]$_.CommandLine) $release) -and
        (Has-Text ([string]$_.CommandLine) '--database-path') -and
        (Has-Text ([string]$_.CommandLine) '--project-id') -and
        (Has-Text ([string]$_.CommandLine) $ProjectId)
    }
)

if ($live.Count -ge 1) {
    $healthAgeSeconds = $null
    if ($HealthPath -and (Test-Path -LiteralPath $HealthPath -PathType Leaf)) {
        $healthAgeSeconds = [math]::Max(0, ((Get-Date) - (Get-Item -LiteralPath $HealthPath).LastWriteTime).TotalSeconds)
    }
    [pscustomobject]@{
        status='HEALTHY_RUNNING'
        main_task=$MainTaskName
        task_state=[string]$task.State
        live_processes=$live.Count
        health_age_seconds=$healthAgeSeconds
    }
    exit 0
}

# The watchdog is deliberately a different Scheduled Task/process tree from
# the daemon.  It never edits SQLite and never fabricates liveness.  When the
# daemon process is absent it only demand-starts the registered main task.
if ($task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $MainTaskName -ErrorAction SilentlyContinue
}
Start-ScheduledTask -TaskName $MainTaskName -ErrorAction Stop
[pscustomobject]@{
    status='RECOVERY_START_REQUESTED'
    main_task=$MainTaskName
    prior_task_state=[string]$task.State
    live_processes=0
}
