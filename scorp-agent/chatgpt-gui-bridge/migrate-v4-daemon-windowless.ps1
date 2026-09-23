[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [string]$TaskName = 'ScorpV4PersistentRuntime',
    [string]$WatchdogTaskName,
    [string]$Python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe',
    [string]$BackupDirectory = 'C:\ScorpAgent\runtime-v4\history',
    [switch]$AllowWatchdogWithoutPythonBinding,
    [switch]$EnsureWatchdogHidden,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$migrationSucceeded = $false
$mainXml = $null
$watchdogXml = $null
$mainWasRunning = $false
$watchdogWasRunning = $false

function Resolve-WindowlessPython([string]$Executable) {
    $fullPath = [IO.Path]::GetFullPath($Executable)
    $leaf = [IO.Path]::GetFileName($fullPath)
    if ($leaf -ieq 'pythonw.exe') {
        $windowlessPath = $fullPath
    } elseif ($leaf -ieq 'python.exe') {
        $windowlessPath = Join-Path (Split-Path -Parent $fullPath) 'pythonw.exe'
    } else {
        throw 'PYTHON_RUNTIME_EXECUTABLE_UNSUPPORTED'
    }
    if (-not (Test-Path -LiteralPath $windowlessPath -PathType Leaf)) {
        throw 'PYTHONW_RUNTIME_MISSING'
    }
    return [IO.Path]::GetFullPath($windowlessPath)
}

function Ensure-HiddenPowerShellArguments([string]$Arguments) {
    $result = $Arguments.Trim()
    if ($result -notmatch '(?i)(^|\s)-NonInteractive(\s|$)') {
        $result = '-NonInteractive ' + $result
    }
    if ($result -notmatch '(?i)(^|\s)-WindowStyle\s+Hidden(\s|$)') {
        $result = '-WindowStyle Hidden ' + $result
    }
    return $result.Trim()
}

function New-PreservedTaskAction([string]$Execute, [string]$Argument, [string]$WorkingDirectory) {
    if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
        return New-ScheduledTaskAction -Execute $Execute -Argument $Argument
    }
    return New-ScheduledTaskAction -Execute $Execute -Argument $Argument -WorkingDirectory $WorkingDirectory
}

if (-not $WatchdogTaskName) { $WatchdogTaskName = "$TaskName-Watchdog" }
$windowlessPython = Resolve-WindowlessPython $Python
$backupRoot = [IO.Path]::GetFullPath($BackupDirectory)
$stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmssfff')

try {
    $main = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $watchdog = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop
    if (@($main.Actions).Count -ne 1 -or -not $main.Actions[0].Execute) { throw 'MAIN_TASK_ACTION_INVALID' }
    if (@($watchdog.Actions).Count -ne 1 -or -not $watchdog.Actions[0].Execute) { throw 'WATCHDOG_TASK_ACTION_INVALID' }
    $mainAction = $main.Actions[0]
    $watchdogAction = $watchdog.Actions[0]
    $watchdogArguments = [string]$watchdogAction.Arguments
    $watchdogHasPythonBinding = $watchdogArguments -match '(?i)-PythonExecutable\s+"[^"]+"'
    if (-not $watchdogHasPythonBinding -and -not $AllowWatchdogWithoutPythonBinding) {
        throw 'WATCHDOG_PYTHON_ARGUMENT_MISSING'
    }

    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $mainBackupPath = Join-Path $backupRoot ("{0}-{1}.xml" -f $TaskName, $stamp)
    $watchdogBackupPath = Join-Path $backupRoot ("{0}-{1}.xml" -f $WatchdogTaskName, $stamp)
    $mainXml = Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $watchdogXml = Export-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop
    Set-Content -LiteralPath $mainBackupPath -Value $mainXml -Encoding Unicode -NoNewline
    Set-Content -LiteralPath $watchdogBackupPath -Value $watchdogXml -Encoding Unicode -NoNewline
    $mainWasRunning = ([string]$main.State -eq 'Running')
    $watchdogWasRunning = ([string]$watchdog.State -eq 'Running')

    if ($PSCmdlet.ShouldProcess($TaskName, 'Rebind V4 daemon to pythonw.exe')) {
        if ($watchdogWasRunning) { Stop-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop }
        if ($mainWasRunning) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop }

        $newMainAction = New-PreservedTaskAction `
            -Execute $windowlessPython `
            -Argument ([string]$mainAction.Arguments) `
            -WorkingDirectory ([string]$mainAction.WorkingDirectory)
        Set-ScheduledTask -TaskName $TaskName -Action $newMainAction | Out-Null

        $newWatchdogArguments = $watchdogArguments
        if ($watchdogHasPythonBinding) {
            $newWatchdogArguments = [regex]::Replace(
                $newWatchdogArguments,
                '(?i)(-PythonExecutable\s+)"[^"]+"',
                ('$1"{0}"' -f $windowlessPython)
            )
        }
        if ($EnsureWatchdogHidden) {
            $newWatchdogArguments = Ensure-HiddenPowerShellArguments $newWatchdogArguments
        }
        $newWatchdogAction = New-PreservedTaskAction `
            -Execute ([string]$watchdogAction.Execute) `
            -Argument $newWatchdogArguments `
            -WorkingDirectory ([string]$watchdogAction.WorkingDirectory)
        Set-ScheduledTask -TaskName $WatchdogTaskName -Action $newWatchdogAction | Out-Null

        $registeredMain = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $registeredWatchdog = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop
        if ([IO.Path]::GetFullPath([string]$registeredMain.Actions[0].Execute) -ine $windowlessPython) {
            throw 'REGISTERED_MAIN_WINDOWLESS_PYTHON_MISMATCH'
        }
        if ($watchdogHasPythonBinding -and [string]$registeredWatchdog.Actions[0].Arguments -notmatch [regex]::Escape($windowlessPython)) {
            throw 'REGISTERED_WATCHDOG_WINDOWLESS_PYTHON_MISSING'
        }
        if ($EnsureWatchdogHidden -and [string]$registeredWatchdog.Actions[0].Arguments -notmatch '(?i)-WindowStyle\s+Hidden') {
            throw 'REGISTERED_WATCHDOG_WINDOW_HIDDEN_MISSING'
        }

        if ($mainWasRunning -or $Start) { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
        if ($watchdogWasRunning -or $Start) { Start-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop }
    }

    $migrationSucceeded = $true
    [pscustomobject]@{
        status = 'WINDOWLESS_TASK_REBOUND'
        task_name = $TaskName
        watchdog_task_name = $WatchdogTaskName
        windowless_python = $windowlessPython
        watchdog_python_bound = [bool]$watchdogHasPythonBinding
        watchdog_hidden = [bool]$EnsureWatchdogHidden
        main_backup = $mainBackupPath
        watchdog_backup = $watchdogBackupPath
        main_restarted = [bool]($mainWasRunning -or $Start)
        watchdog_restarted = [bool]($watchdogWasRunning -or $Start)
    }
}
finally {
    if (-not $migrationSucceeded -and $mainXml -and $watchdogXml) {
        try { Register-ScheduledTask -TaskName $TaskName -Xml $mainXml -Force | Out-Null } catch {}
        try { Register-ScheduledTask -TaskName $WatchdogTaskName -Xml $watchdogXml -Force | Out-Null } catch {}
        if ($mainWasRunning) { try { Start-ScheduledTask -TaskName $TaskName } catch {} }
        if ($watchdogWasRunning) { try { Start-ScheduledTask -TaskName $WatchdogTaskName } catch {} }
    }
}
