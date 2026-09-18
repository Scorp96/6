[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [Parameter(Mandatory=$true)][string]$AllowedRoot,
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [Parameter(Mandatory=$true)][string]$DriverStatePath,
    [string]$MasterSessionId = 'master-a-runtime',
    [ValidateRange(-1, [int]::MaxValue)][int]$DaemonEpoch = -1,
    [string]$Python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe',
    [string]$TaskName = 'SCORP_V4_DAEMON',
    [string]$HealthPath,
    [ValidateRange(15, 3600)][int]$WatchdogMaxHealthAgeSeconds = 90,
    [ValidateRange(5, 600)][int]$WatchdogStartupGraceSeconds = 60,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$installSucceeded = $false
$priorTaskXml = $null
$priorTaskState = $null
$taskExisted = $false
$priorWatchdogXml = $null
$priorWatchdogState = $null
$watchdogTaskExisted = $false
$resolvedTaskName = $TaskName
$watchdogTaskName = "$resolvedTaskName-Watchdog"

function Assert-NoQuote([string]$Value, [string]$Name) {
    if ($Value.Contains('"')) { throw "$Name contains an unsupported quote" }
}

try {
    foreach ($pair in @(
        @{Value=$DatabasePath;Name='DatabasePath'},
        @{Value=$AllowedRoot;Name='AllowedRoot'},
        @{Value=$ProjectId;Name='ProjectId'},
        @{Value=$DriverStatePath;Name='DriverStatePath'},
        @{Value=$MasterSessionId;Name='MasterSessionId'},
        @{Value=$Python;Name='Python'},
        @{Value=$TaskName;Name='TaskName'},
        @{Value=$watchdogTaskName;Name='WatchdogTaskName'}
    )) { Assert-NoQuote $pair.Value $pair.Name }

    $database = [IO.Path]::GetFullPath($DatabasePath)
    $allowed = [IO.Path]::GetFullPath($AllowedRoot)
    $driverState = [IO.Path]::GetFullPath($DriverStatePath)
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { throw 'STATE_DATABASE_MISSING' }
    if (-not (Test-Path -LiteralPath $allowed -PathType Container)) { throw 'ALLOWED_ROOT_MISSING' }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'PYTHON_RUNTIME_MISSING' }

    $daemonScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'tools\v4_release_runtime.py'))
    if (-not (Test-Path -LiteralPath $daemonScript -PathType Leaf)) { throw 'V4_RELEASE_RUNTIME_SCRIPT_MISSING' }
    $watchdogScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'watch-v4-daemon.ps1'))
    if (-not (Test-Path -LiteralPath $watchdogScript -PathType Leaf)) { throw 'V4_WATCHDOG_SCRIPT_MISSING' }
    if (-not $HealthPath) { $HealthPath = [IO.Path]::ChangeExtension($database, '.daemon-health.json') }
    Assert-NoQuote $HealthPath 'HealthPath'

    $existingWatchdog = Get-ScheduledTask -TaskName $watchdogTaskName -ErrorAction SilentlyContinue
    if ($null -ne $existingWatchdog) {
        $watchdogTaskExisted = $true
        $priorWatchdogXml = Export-ScheduledTask -TaskName $watchdogTaskName
        $priorWatchdogState = $existingWatchdog.State
        Stop-ScheduledTask -TaskName $watchdogTaskName -ErrorAction SilentlyContinue
    }

    $existing = Get-ScheduledTask -TaskName $resolvedTaskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $taskExisted = $true
        $priorTaskXml = Export-ScheduledTask -TaskName $resolvedTaskName
        $priorTaskState = (Get-ScheduledTask -TaskName $resolvedTaskName).State
        Stop-ScheduledTask -TaskName $resolvedTaskName -ErrorAction SilentlyContinue
    }

    $argumentList = @(
        '-B', ('"{0}"' -f $daemonScript),
        '--database-path', ('"{0}"' -f $database),
        '--allowed-root', ('"{0}"' -f $allowed),
        '--project-id', ('"{0}"' -f $ProjectId),
        '--health-path', ('"{0}"' -f [IO.Path]::GetFullPath($HealthPath)),
        '--forever'
    )
    if ($DaemonEpoch -ge 0) {
        $argumentList = @(
            '-B', ('"{0}"' -f $daemonScript),
            '--database-path', ('"{0}"' -f $database),
            '--allowed-root', ('"{0}"' -f $allowed),
            '--project-id', ('"{0}"' -f $ProjectId),
            '--daemon-epoch', [string]$DaemonEpoch,
            '--health-path', ('"{0}"' -f [IO.Path]::GetFullPath($HealthPath)),
            '--forever'
        )
    }
    $argumentList += @(
        '--active-controller',
        '--driver-state-path', ('"{0}"' -f $driverState),
        '--supervise-master',
        '--master-session-id', ('"{0}"' -f $MasterSessionId)
    )
    $arguments = $argumentList -join ' '
    $action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory (Split-Path -Parent $daemonScript)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    $logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $startup = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)

    $watchdogArguments = @(
        '-NoProfile',
        '-NonInteractive',
        '-WindowStyle', 'Hidden',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $watchdogScript),
        '-MainTaskName', ('"{0}"' -f $resolvedTaskName),
        '-ProjectId', ('"{0}"' -f $ProjectId),
        '-ReleaseRuntimeScript', ('"{0}"' -f $daemonScript),
        '-PythonExecutable', ('"{0}"' -f [IO.Path]::GetFullPath($Python)),
        '-DatabasePath', ('"{0}"' -f $database),
        '-HealthPath', ('"{0}"' -f [IO.Path]::GetFullPath($HealthPath)),
        '-MaxHealthAgeSeconds', [string]$WatchdogMaxHealthAgeSeconds,
        '-StartupGraceSeconds', [string]$WatchdogStartupGraceSeconds
    ) -join ' '
    $watchdogAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $watchdogArguments -WorkingDirectory $PSScriptRoot
    $watchdogPeriodic = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
    $watchdogSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

    if ($PSCmdlet.ShouldProcess($resolvedTaskName, 'Register SCORP V4 daemon scheduled task')) {
        Register-ScheduledTask -TaskName $resolvedTaskName -Action $action -Trigger @($logon, $startup) -Principal $principal -Settings $settings -Description 'SCORP V4 SQLite local daemon' -Force | Out-Null
        Register-ScheduledTask -TaskName $watchdogTaskName -Action $watchdogAction -Trigger @($logon, $startup, $watchdogPeriodic) -Principal $principal -Settings $watchdogSettings -Description 'SCORP V4 independent daemon recovery watchdog' -Force | Out-Null
        $registered = Get-ScheduledTask -TaskName $resolvedTaskName -ErrorAction Stop
        if ($registered.Actions[0].Execute -ne $Python) { throw 'REGISTERED_PYTHON_MISMATCH' }
        $registeredWatchdog = Get-ScheduledTask -TaskName $watchdogTaskName -ErrorAction Stop
        if ($registeredWatchdog.Actions[0].Execute -ne 'powershell.exe') { throw 'REGISTERED_WATCHDOG_EXECUTABLE_MISMATCH' }
        if ($Start) {
            Start-ScheduledTask -TaskName $resolvedTaskName
            Start-ScheduledTask -TaskName $watchdogTaskName
        }
    }
    $installSucceeded = $true
    $epochValue = if ($DaemonEpoch -ge 0) { $DaemonEpoch } else { $null }
    $epochMode = if ($DaemonEpoch -ge 0) { 'EXPECTED' } else { 'ACQUIRE_CURRENT' }
    [pscustomobject]@{ task_name=$resolvedTaskName; watchdog_task_name=$watchdogTaskName; database=$database; project_id=$ProjectId; driver_state_path=$driverState; master_session_id=$MasterSessionId; active_controller=$true; supervise_master=$true; daemon_epoch=$epochValue; epoch_mode=$epochMode; started=[bool]$Start; script=$daemonScript; watchdog_script=$watchdogScript; watchdog_max_health_age_seconds=$WatchdogMaxHealthAgeSeconds; watchdog_startup_grace_seconds=$WatchdogStartupGraceSeconds }
}
finally {
    if (-not $installSucceeded) {
        try { Stop-ScheduledTask -TaskName $watchdogTaskName -ErrorAction SilentlyContinue } catch {}
        try { Unregister-ScheduledTask -TaskName $watchdogTaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        try { Stop-ScheduledTask -TaskName $resolvedTaskName -ErrorAction SilentlyContinue } catch {}
        try { Unregister-ScheduledTask -TaskName $resolvedTaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        if ($taskExisted -and $priorTaskXml) {
            try { Register-ScheduledTask -TaskName $resolvedTaskName -Xml $priorTaskXml -Force | Out-Null } catch {}
            if ($priorTaskState -eq 'Running') { try { Start-ScheduledTask -TaskName $resolvedTaskName } catch {} }
        }
        if ($watchdogTaskExisted -and $priorWatchdogXml) {
            try { Register-ScheduledTask -TaskName $watchdogTaskName -Xml $priorWatchdogXml -Force | Out-Null } catch {}
            if ($priorWatchdogState -eq 'Running') { try { Start-ScheduledTask -TaskName $watchdogTaskName } catch {} }
        }
    }
}
