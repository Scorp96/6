param(
  [string]$TargetTaskName = 'ScorpChatGptGuiBridge',
  [string]$BridgeWorkerPath = 'C:\ScorpAgent\chatgpt-gui-bridge\bridge_worker.py',
  [string]$StateDir = 'C:\ScorpAgent\chatgpt-gui-bridge-state',
  [int]$MaxHealthAgeSeconds = 120
)
$ErrorActionPreference = 'Stop'
$healthPath = Join-Path $StateDir 'watchdog-health.json'
$bridgeHealthPath = Join-Path $StateDir 'health.json'

function Write-WatchdogHealth([string]$Status, [string]$Detail, [int]$ProcessCount) {
  New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
  $value = [ordered]@{
    protocol_version = 'scorp.gui-bridge-watchdog/health-v1'
    status = $Status
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
    target_task = $TargetTaskName
    process_count = $ProcessCount
    detail = $Detail
  }
  $json = $value | ConvertTo-Json -Compress
  $tmp = $healthPath + '.tmp'
  [IO.File]::WriteAllText($tmp, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $tmp -Destination $healthPath -Force
}

function Test-BridgeFunctionalHealth {
  if (-not (Test-Path -LiteralPath $bridgeHealthPath -PathType Leaf)) {
    return [pscustomobject]@{ Healthy = $false; Detail = 'bridge functional health file is missing' }
  }
  try {
    $value = Get-Content -LiteralPath $bridgeHealthPath -Raw | ConvertFrom-Json
    if ([string]$value.protocol_version -ne 'scorp.gui-bridge/health-v1') {
      return [pscustomobject]@{ Healthy = $false; Detail = 'bridge functional health protocol mismatch' }
    }
    $heartbeat = [DateTimeOffset]::Parse([string]$value.heartbeat_at).ToUniversalTime()
    $age = ([DateTimeOffset]::UtcNow - $heartbeat).TotalSeconds
    if ($age -lt 0 -or $age -gt $MaxHealthAgeSeconds) {
      return [pscustomobject]@{ Healthy = $false; Detail = ('bridge functional heartbeat stale age={0:N0}s' -f $age) }
    }
    if ([string]$value.status -eq 'ERROR') {
      return [pscustomobject]@{ Healthy = $false; Detail = 'bridge functional health reports ERROR' }
    }
    return [pscustomobject]@{ Healthy = $true; Detail = ('functional bridge heartbeat age={0:N0}s status={1}' -f $age, [string]$value.status) }
  } catch {
    return [pscustomobject]@{ Healthy = $false; Detail = ('bridge functional health unreadable: {0}' -f $_.Exception.Message) }
  }
}

$task = Get-ScheduledTask -TaskName $TargetTaskName -ErrorAction Stop
$workers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop | Where-Object {
  ([string]$_.CommandLine).Contains($BridgeWorkerPath)
})

if ($workers.Count -gt 0) {
  $roots = @()
  foreach ($worker in $workers) {
    $parent = $null
    try { $parent = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $worker.ParentProcessId) -ErrorAction Stop } catch {}
    if ($parent -and [string]$parent.Name -eq 'svchost.exe') {
      $roots += $worker
    }
  }
  if ($roots.Count -eq 1) {
    $functional = Test-BridgeFunctionalHealth
    if ($functional.Healthy) {
      Write-WatchdogHealth 'WATCHDOG_HEALTHY' $functional.Detail $workers.Count
      Write-Output 'WATCHDOG_HEALTHY'
      exit 0
    }
    Write-WatchdogHealth 'WATCHDOG_STALE_HEALTH' $functional.Detail $workers.Count
  }
  elseif ($roots.Count -ne 0) {
    Write-WatchdogHealth 'WATCHDOG_ORPHAN_BLOCKED' ("bridge processes present without exactly one scheduler root; roots={0}" -f $roots.Count) $workers.Count
    Write-Output 'WATCHDOG_ORPHAN_BLOCKED'
    exit 0
  }
}

if ([string]$task.State -eq 'Running') {
  Stop-ScheduledTask -TaskName $TargetTaskName
  Start-Sleep -Milliseconds 500
}
Start-ScheduledTask -TaskName $TargetTaskName
Start-Sleep -Seconds 2
$after = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop | Where-Object {
  ([string]$_.CommandLine).Contains($BridgeWorkerPath)
})
if ($after.Count -lt 1) {
  Write-WatchdogHealth 'WATCHDOG_RESTART_FAILED' 'target task start produced no bridge process' 0
  throw 'WATCHDOG_RESTART_FAILED'
}
Write-WatchdogHealth 'WATCHDOG_RESTARTED' 'bridge process tree was absent and target task was started' $after.Count
Write-Output 'WATCHDOG_RESTARTED'
