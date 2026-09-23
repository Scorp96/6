param(
  [string]$SourceDir = $PSScriptRoot,
  [string]$InstallDir = 'C:\ScorpAgent\chatgpt-gui-bridge',
  [string]$StateDir = 'C:\ScorpAgent\chatgpt-gui-bridge-state',
  [string]$RuntimeDir = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime',
  [string]$TaskName = 'ScorpChatGptGuiBridge',
  [string]$WatchdogTaskName = 'ScorpChatGptGuiBridgeWatchdog'
)
$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = 'C:\ScorpAgent\backups'
$uv = 'C:\Users\scorp\AppData\Local\Microsoft\WinGet\Links\uv.exe'
$python = Join-Path $RuntimeDir 'Scripts\python.exe'
$V3ProjectRoot = 'C:\ScorpAgent\state-v3\active'
$files = @('bridge_core.py','gui_transport.py','bridge_worker.py','role_relay.py','run-bridge.ps1','production_v3_runtime.py','chat_resource_manager_v3.py','worker_conversation_pool_v3.py','actor_gui_backend_v3.py','actor_response_journal_v3.py','chrome_use_cli_v3.py','chrome_use_actor_driver_v3.py','v4_auth.py','continuation_watchdog_v3.py','durable_actor_transport_v3.py','master_state_transition_v3.py','master_window_lease_v3.py','master_worker_coordinator_v3.py','master_worker_relay_v3.py','parallel_master_worker_relay_v3.py','project_state_v3.py','session_registry_v3.py','turn_scheduler_v3.py','windows_mcp_actor_driver_v3.py','worker_event_pump_v3.py','worker_result_guard_v3.py','worker_event_queue_v3.py','bridge-watchdog.ps1','project_bootstrap_v3.py','project-bootstrap-template.json','project_lifecycle_v3.py','install-bridge.ps1')

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

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
New-Item -ItemType Directory -Path $V3ProjectRoot -Force | Out-Null

# Complete all source/runtime preflight before interrupting the live task pair.
foreach ($name in $files) {
  $src = Join-Path $SourceDir $name
  if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "SOURCE_FILE_MISSING $name" }
}
if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw 'UV_MISSING' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  $savedEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $uv venv --python 3.14 $RuntimeDir
  $nativeRc = $LASTEXITCODE
  $ErrorActionPreference = $savedEap
  if ($nativeRc -ne 0) { throw 'BRIDGE_RUNTIME_CREATE_FAILED' }
}
$windowlessPython = Resolve-WindowlessPython $python
$savedEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $uv pip install --python $python 'mcp==2.2.0'
$nativeRc = $LASTEXITCODE
$ErrorActionPreference = $savedEap
if ($nativeRc -ne 0) { throw 'BRIDGE_RUNTIME_DEPENDENCY_FAILED' }

# Snapshot both scheduled tasks and the installed files before mutation.
$existingMain = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$existingWatchdog = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction SilentlyContinue
$mainTaskExisted = $null -ne $existingMain
$watchdogTaskExisted = $null -ne $existingWatchdog
$mainTaskWasRunning = $mainTaskExisted -and ([string]$existingMain.State -eq 'Running')
$watchdogTaskWasRunning = $watchdogTaskExisted -and ([string]$existingWatchdog.State -eq 'Running')
$mainTaskBackupXml = $null
$watchdogTaskBackupXml = $null
if ($mainTaskExisted) {
  $mainTaskBackupXml = Export-ScheduledTask -TaskName $TaskName
  [IO.File]::WriteAllText((Join-Path $backupRoot "$TaskName-$stamp.xml"), $mainTaskBackupXml, [Text.UTF8Encoding]::new($false))
}
if ($watchdogTaskExisted) {
  $watchdogTaskBackupXml = Export-ScheduledTask -TaskName $WatchdogTaskName
  [IO.File]::WriteAllText((Join-Path $backupRoot "$WatchdogTaskName-$stamp.xml"), $watchdogTaskBackupXml, [Text.UTF8Encoding]::new($false))
}
$installDirExisted = Test-Path -LiteralPath $InstallDir -PathType Container
$backupDir = Join-Path $backupRoot "chatgpt-gui-bridge-$stamp"
if ($installDirExisted) {
  New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
  Get-ChildItem -LiteralPath $InstallDir -Force | Copy-Item -Destination $backupDir -Recurse -Force -ErrorAction Stop
}

$installSucceeded = $false
try {
  # The watchdog must be stopped first or it can race the installer by restarting main.
  if ($watchdogTaskWasRunning) { Stop-ScheduledTask -TaskName $WatchdogTaskName }
  if ($mainTaskWasRunning) { Stop-ScheduledTask -TaskName $TaskName }
  Start-Sleep -Milliseconds 500

  New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
  foreach ($name in $files) {
    $src = Join-Path $SourceDir $name
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "SOURCE_FILE_MISSING_DURING_INSTALL $name" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $InstallDir $name) -Force -ErrorAction Stop
  }

  $worker = Join-Path $InstallDir 'bridge_worker.py'
  $arguments = @(
    "`"$worker`"",
    '--orchestrator-root','C:\ScorpAgent\orchestrator-v1',
    '--state-root','C:\ScorpAgent\state-v4',
    '--bridge-root',"`"$StateDir`"",
    '--control-repo','Scorp96/scorp-control-plane',
    '--trusted-actor','Scorp96',
    '--v3-project-root',"`"$V3ProjectRoot`"",
    '--v3-max-inflight','5',
    '--v3-max-workers','4',
    '--poll-seconds','10'
  ) -join ' '
  $action = New-ScheduledTaskAction -Execute $windowlessPython -Argument $arguments
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

  $watchdogScript = Join-Path $InstallDir 'bridge-watchdog.ps1'
  $watchdogArguments = @(
    '-NoProfile',
    '-NonInteractive',
    '-WindowStyle','Hidden',
    '-ExecutionPolicy','Bypass',
    '-File',"`"$watchdogScript`"",
    '-TargetTaskName',"`"$TaskName`"",
    '-BridgeWorkerPath',"`"$worker`"",
    '-StateDir',"`"$StateDir`""
  ) -join ' '
  $watchdogAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $watchdogArguments
  $watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
  $watchdogSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $WatchdogTaskName -Action $watchdogAction -Trigger $watchdogTrigger -Principal $principal -Settings $watchdogSettings -Force | Out-Null

  Start-ScheduledTask -TaskName $TaskName
  Start-Sleep -Seconds 3
  Start-ScheduledTask -TaskName $WatchdogTaskName
  Start-Sleep -Seconds 2

  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
  $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
  $actualAction = $task.Actions | Select-Object -First 1
  $watchdogTask = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop
  if ([string]$task.State -ne 'Running') { throw ('BRIDGE_INSTALL_MAIN_NOT_RUNNING ' + [string]$task.State) }
  if ([string]$actualAction.Execute -ne $windowlessPython) { throw 'BRIDGE_INSTALL_ACTION_EXECUTE_MISMATCH' }
  if (-not ([string]$actualAction.Arguments).Contains('--v3-project-root')) { throw 'BRIDGE_INSTALL_V3_BINDING_MISSING' }

  $installSucceeded = $true
  [pscustomobject]@{
    status='BRIDGE_INSTALL_PASS'
    task=$TaskName
    state=[string]$task.State
    last_result=$info.LastTaskResult
    execute=[string]$actualAction.Execute
    arguments=[string]$actualAction.Arguments
    runtime=$windowlessPython
    install_dir=$InstallDir
    state_dir=$StateDir
    v3_project_root=$V3ProjectRoot
    watchdog_task=$WatchdogTaskName
    watchdog_state=[string]$watchdogTask.State
  } | ConvertTo-Json -Compress
}
catch {
  $installError = $_
  $rollbackErrors = [System.Collections.Generic.List[string]]::new()
  $rollbackEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'

  try { Stop-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction SilentlyContinue } catch { $rollbackErrors.Add('STOP_WATCHDOG:' + $_.Exception.Message) }
  try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { $rollbackErrors.Add('STOP_MAIN:' + $_.Exception.Message) }

  try {
    if (Test-Path -LiteralPath $InstallDir) { Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction Stop }
    if ($installDirExisted) {
      New-Item -ItemType Directory -Path $InstallDir -Force -ErrorAction Stop | Out-Null
      Get-ChildItem -LiteralPath $backupDir -Force | Copy-Item -Destination $InstallDir -Recurse -Force -ErrorAction Stop
    }
  } catch { $rollbackErrors.Add('RESTORE_FILES:' + $_.Exception.Message) }

  try {
    if ($mainTaskExisted) {
      Register-ScheduledTask -TaskName $TaskName -Xml $mainTaskBackupXml -Force | Out-Null
    } else {
      Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
  } catch { $rollbackErrors.Add('RESTORE_MAIN_TASK:' + $_.Exception.Message) }
  try {
    if ($watchdogTaskExisted) {
      Register-ScheduledTask -TaskName $WatchdogTaskName -Xml $watchdogTaskBackupXml -Force | Out-Null
    } else {
      Unregister-ScheduledTask -TaskName $WatchdogTaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
  } catch { $rollbackErrors.Add('RESTORE_WATCHDOG_TASK:' + $_.Exception.Message) }

  if (-not $mainTaskExisted) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue }
  if (-not $watchdogTaskExisted) { Unregister-ScheduledTask -TaskName $WatchdogTaskName -Confirm:$false -ErrorAction SilentlyContinue }

  if ($mainTaskWasRunning) {
    try { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch { $rollbackErrors.Add('START_MAIN:' + $_.Exception.Message) }
  }
  if ($watchdogTaskWasRunning) {
    try { Start-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction Stop } catch { $rollbackErrors.Add('START_WATCHDOG:' + $_.Exception.Message) }
  }

  $ErrorActionPreference = $rollbackEap
  if ($rollbackErrors.Count -gt 0) {
    throw ('BRIDGE_INSTALL_ROLLBACK_FAILED ' + ($rollbackErrors -join ' | ') + ' ORIGINAL=' + $installError.Exception.Message)
  }
  throw $installError
}
finally {
  if (-not $installSucceeded) {
    # All rollback work is performed in catch. This finally marker makes the transaction state explicit.
  }
}
