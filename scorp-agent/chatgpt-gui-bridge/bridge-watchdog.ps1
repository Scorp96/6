param(
  [string]$TargetTaskName = 'ScorpChatGptGuiBridge',
  [string]$BridgeWorkerPath = 'C:\ScorpAgent\chatgpt-gui-bridge\bridge_worker.py',
  [string]$StateDir = 'C:\ScorpAgent\chatgpt-gui-bridge-state'
)
$ErrorActionPreference = 'Stop'
$healthPath = Join-Path $StateDir 'watchdog-health.json'

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
    Write-WatchdogHealth 'WATCHDOG_HEALTHY' 'scheduler-owned bridge process tree present' $workers.Count
    Write-Output 'WATCHDOG_HEALTHY'
    exit 0
  }
  Write-WatchdogHealth 'WATCHDOG_ORPHAN_BLOCKED' ("bridge processes present without exactly one scheduler root; roots={0}" -f $roots.Count) $workers.Count
  Write-Output 'WATCHDOG_ORPHAN_BLOCKED'
  exit 0
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