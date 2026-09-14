[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [Parameter(Mandatory=$true)][string]$DatabasePath,
    [Parameter(Mandatory=$true)][string]$AllowedRoot,
    [Parameter(Mandatory=$true)][string]$ProjectId,
    [ValidateRange(-1, [int]::MaxValue)][int]$DaemonEpoch = -1,
    [string]$Python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe',
    [string]$TaskName = 'SCORP_V4_DAEMON',
    [string]$HealthPath,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$installSucceeded = $false
$priorTaskXml = $null
$priorTaskState = $null
$taskExisted = $false
$resolvedTaskName = $TaskName

function Assert-NoQuote([string]$Value, [string]$Name) {
    if ($Value.Contains('"')) { throw "$Name contains an unsupported quote" }
}

try {
    foreach ($pair in @(
        @{Value=$DatabasePath;Name='DatabasePath'},
        @{Value=$AllowedRoot;Name='AllowedRoot'},
        @{Value=$ProjectId;Name='ProjectId'},
        @{Value=$Python;Name='Python'},
        @{Value=$TaskName;Name='TaskName'}
    )) { Assert-NoQuote $pair.Value $pair.Name }

    $database = [IO.Path]::GetFullPath($DatabasePath)
    $allowed = [IO.Path]::GetFullPath($AllowedRoot)
    if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { throw 'STATE_DATABASE_MISSING' }
    if (-not (Test-Path -LiteralPath $allowed -PathType Container)) { throw 'ALLOWED_ROOT_MISSING' }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'PYTHON_RUNTIME_MISSING' }

    $daemonScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'tools\v4_daemon_runtime.py'))
    if (-not (Test-Path -LiteralPath $daemonScript -PathType Leaf)) { throw 'V4_DAEMON_SCRIPT_MISSING' }
    if (-not $HealthPath) { $HealthPath = [IO.Path]::ChangeExtension($database, '.daemon-health.json') }
    Assert-NoQuote $HealthPath 'HealthPath'

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
    $arguments = $argumentList -join ' '
    $action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory (Split-Path -Parent $daemonScript)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    $logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $startup = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)

    if ($PSCmdlet.ShouldProcess($resolvedTaskName, 'Register SCORP V4 daemon scheduled task')) {
        Register-ScheduledTask -TaskName $resolvedTaskName -Action $action -Trigger @($logon, $startup) -Principal $principal -Settings $settings -Description 'SCORP V4 SQLite local daemon' -Force | Out-Null
        $registered = Get-ScheduledTask -TaskName $resolvedTaskName -ErrorAction Stop
        if ($registered.Actions[0].Execute -ne $Python) { throw 'REGISTERED_PYTHON_MISMATCH' }
        if ($Start) { Start-ScheduledTask -TaskName $resolvedTaskName }
    }
    $installSucceeded = $true
    $epochValue = if ($DaemonEpoch -ge 0) { $DaemonEpoch } else { $null }
    $epochMode = if ($DaemonEpoch -ge 0) { 'EXPECTED' } else { 'ACQUIRE_CURRENT' }
    [pscustomobject]@{ task_name=$resolvedTaskName; database=$database; project_id=$ProjectId; daemon_epoch=$epochValue; epoch_mode=$epochMode; started=[bool]$Start; script=$daemonScript }
}
finally {
    if (-not $installSucceeded) {
        try { Stop-ScheduledTask -TaskName $resolvedTaskName -ErrorAction SilentlyContinue } catch {}
        try { Unregister-ScheduledTask -TaskName $resolvedTaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        if ($taskExisted -and $priorTaskXml) {
            try { Register-ScheduledTask -TaskName $resolvedTaskName -Xml $priorTaskXml -Force | Out-Null } catch {}
            if ($priorTaskState -eq 'Running') { try { Start-ScheduledTask -TaskName $resolvedTaskName } catch {} }
        }
    }
}
