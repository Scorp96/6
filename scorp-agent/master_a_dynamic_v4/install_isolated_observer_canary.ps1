param(
  [ValidateSet('Inspect','DryRun','InstallDisabled','Enable','Disable','Remove')]
  [string]$Mode = 'Inspect',
  [ValidateSet(15,25)][int]$IntervalMinutes = 15,
  [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ExpectedCommit
)
$ErrorActionPreference = 'Stop'
$repo = 'C:\ScorpAgent\experiments\r2-gpt-session-audit-20261009'
$root = 'C:\ScorpAgent\experiments'
$taskName = 'ScorpR2GPTObserver' + $IntervalMinutes + 'mCanary'
$workspace = Join-Path $root (
  if ($IntervalMinutes -eq 15) {
    'r2-observer-state-20261009'
  } else {
    'r2-observer-state-25m-20261009'
  }
)
$runner = Join-Path $repo 'scorp-agent\master_a_dynamic_v4\run_isolated_observer_canary.ps1'
$scriptInstaller = Join-Path $repo 'scorp-agent\master_a_dynamic_v4\install_isolated_observer_canary.ps1'
$python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe'
$actionExecute = 'powershell.exe'
$actionArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
  $runner + '" -IntervalMinutes ' + $IntervalMinutes + ' -ExpectedCommit ' + $ExpectedCommit

function Require-Current-Pinned-Checkout {
  if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw 'ISOLATED_RUNNER_MISSING' }
  if (-not (Test-Path -LiteralPath $scriptInstaller -PathType Leaf)) { throw 'ISOLATED_INSTALLER_MISSING' }
  if (-not (Test-Path -LiteralPath $workspace -PathType Container)) { throw 'OBSERVER_WORKSPACE_MISSING' }
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'ISOLATED_PYTHON_MISSING' }
  $actual = (& git -C $repo rev-parse HEAD 2>$null | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $actual -cne $ExpectedCommit) { throw 'PINNED_SHA_MISMATCH' }
  $dirty = (& git -C $repo status --porcelain 2>$null | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'ISOLATED_CHECKOUT_DIRTY' }
}

function Assert-Task-Identity($Task) {
  if ($null -eq $Task) { throw 'ISOLATED_TASK_NOT_FOUND' }
  $actions = @($Task.Actions)
  if ($actions.Count -ne 1) { throw 'ISOLATED_TASK_ACTION_COUNT_INVALID' }
  if ($actions[0].Execute -ine $actionExecute -or $actions[0].Arguments -cne $actionArguments) {
    throw 'ISOLATED_TASK_ACTION_MISMATCH'
  }
  if ([string]$Task.Principal.LogonType -ne 'Interactive') {
    throw 'ISOLATED_TASK_PRINCIPAL_MISMATCH'
  }
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($Mode -eq 'Inspect') {
  $verified = $false
  if ($null -ne $existing) {
    try { Assert-Task-Identity $existing; $verified = $true }
    catch { $verified = $false }
  }
  @{
    protocol_version = 'scorp.observer-task-inspect/1'
    task_name = $taskName
    exists = ($null -ne $existing)
    state = if ($existing) { [string]$existing.State } else { 'ABSENT' }
    identity_verified = $verified
    browser_send_authorized = $false
  } | ConvertTo-Json -Compress
  exit 0
}

Require-Current-Pinned-Checkout

if ($Mode -eq 'DryRun') {
  if ($existing) { throw 'ISOLATED_TASK_NAME_COLLISION' }
  @{
    protocol_version = 'scorp.observer-task-plan/1'
    action = 'INSTALL_DISABLED'
    task_name = $taskName
    observer_interval_minutes = $IntervalMinutes
    scheduler_probe_interval_minutes = 5
    expected_git_sha = $ExpectedCommit
    production_tasks_modified = $false
    browser_send_authorized = $false
  } | ConvertTo-Json -Compress
  exit 0
}

if ($Mode -eq 'InstallDisabled') {
  if ($existing) { throw 'ISOLATED_TASK_NAME_COLLISION' }
  $principalName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
  $action = New-ScheduledTaskAction -Execute $actionExecute -Argument $actionArguments -WorkingDirectory $repo
  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (
    New-TimeSpan -Minutes 5
  ) -RepetitionDuration (New-TimeSpan -Days 3650)
  $principal = New-ScheduledTaskPrincipal -UserId $principalName -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (
    New-TimeSpan -Minutes 2
  ) -RestartCount 0
  $created = $false
  try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -ErrorAction Stop | Out-Null
    $created = $true
    Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
    $installed = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Assert-Task-Identity $installed
    if ([string]$installed.State -ne 'Disabled') { throw 'ISOLATED_TASK_NOT_DISABLED' }
  } catch {
    if ($created) {
      Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    throw
  }
  @{
    status = 'INSTALLED_DISABLED'
    task_name = $taskName
    interval_minutes = $IntervalMinutes
    browser_send_authorized = $false
    production_tasks_modified = $false
  } | ConvertTo-Json -Compress
  exit 0
}

Assert-Task-Identity $existing
if ($Mode -eq 'Enable') {
  Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
} elseif ($Mode -eq 'Disable') {
  Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
} elseif ($Mode -eq 'Remove') {
  if ([string]$existing.State -ne 'Disabled') {
    throw 'DISABLE_ISOLATED_TASK_BEFORE_REMOVAL'
  }
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
}
$after = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
@{
  protocol_version = 'scorp.observer-task-change/1'
  mode = $Mode
  task_name = $taskName
  state = if ($after) { [string]$after.State } else { 'ABSENT' }
  observer_interval_minutes = $IntervalMinutes
  production_tasks_modified = $false
  browser_send_authorized = $false
} | ConvertTo-Json -Compress
