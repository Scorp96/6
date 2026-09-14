param(
  [Parameter(Mandatory=$true)][string]$RequestId,
  [Parameter(Mandatory=$true)][string]$TaskId,
  [Parameter(Mandatory=$true)][string]$ActionId,
  [int]$TimeoutSeconds = 120,
  [string]$AuditPath = 'C:\ProgramData\ScorpAgent\privileged-broker\audit.jsonl',
  [string]$ActiveTaskPath = 'C:\ScorpAgent\state-v4\active-task.json',
  [string]$ReadyPath = 'C:\ScorpAgent\state-v4\crash-watchdog.ready',
  [string]$ResultPath = 'C:\ScorpAgent\state-v4\crash-watchdog.result.json'
)
$ErrorActionPreference = 'Stop'
Remove-Item -LiteralPath $ReadyPath,$ResultPath -Force -ErrorAction SilentlyContinue
$watcher = New-Object IO.FileSystemWatcher((Split-Path $AuditPath -Parent),(Split-Path $AuditPath -Leaf))
$watcher.NotifyFilter = [IO.NotifyFilters]::LastWrite -bor [IO.NotifyFilters]::Size
$watcher.EnableRaisingEvents = $true
[IO.File]::WriteAllText($ReadyPath,[DateTimeOffset]::Now.ToString('o'),(New-Object Text.UTF8Encoding($false)))
$deadline = [DateTimeOffset]::Now.AddSeconds($TimeoutSeconds)
try {
  while([DateTimeOffset]::Now -lt $deadline) {
    $change = $watcher.WaitForChanged([IO.WatcherChangeTypes]::Changed,500)
    if($change.TimedOut){ continue }
    Start-Sleep -Milliseconds 10
    $fs = New-Object IO.FileStream($AuditPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try {
      $sr = New-Object IO.StreamReader($fs,[Text.Encoding]::UTF8)
      try { $text = $sr.ReadToEnd() } finally { $sr.Dispose() }
    } finally { $fs.Dispose() }
    $executed = $false
    foreach($line in ($text -split "`r?`n")) {
      if($line -like ('*'+$RequestId+'*') -and $line -like '*"status":"EXECUTED"*') { $executed = $true; break }
    }
    if(-not $executed){ continue }
    if(-not(Test-Path -LiteralPath $ActiveTaskPath -PathType Leaf)){ throw 'ACTIVE_STATE_MISSING_AFTER_EXECUTED' }
    $state = Get-Content -LiteralPath $ActiveTaskPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if([string]$state.task_id -cne $TaskId -or [string]$state.action_id -cne $ActionId -or [string]$state.action_kind -cne 'privileged_broker'){ throw 'ACTIVE_IDENTITY_MISMATCH' }
    if(Test-Path -LiteralPath ([string]$state.result_path) -PathType Leaf) {
      $o=[ordered]@{status='MISSED_WINDOW_RESULT_ALREADY_DURABLE';runner_pid=[int]$state.child_pid;request_id=$RequestId;at=[DateTimeOffset]::Now.ToString('o')}
      [IO.File]::WriteAllText($ResultPath,($o|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)))
      exit 2
    }
    $runnerPid=[int]$state.child_pid
    if($runnerPid -le 0){ throw 'INVALID_RUNNER_PID' }
    Stop-Process -Id $runnerPid -Force -ErrorAction Stop
    $o=[ordered]@{status='KILLED_AFTER_EXECUTED';runner_pid=$runnerPid;request_id=$RequestId;at=[DateTimeOffset]::Now.ToString('o')}
    [IO.File]::WriteAllText($ResultPath,($o|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)))
    exit 0
  }
  $o=[ordered]@{status='TIMEOUT';request_id=$RequestId;at=[DateTimeOffset]::Now.ToString('o')}
  [IO.File]::WriteAllText($ResultPath,($o|ConvertTo-Json -Compress),(New-Object Text.UTF8Encoding($false)))
  exit 3
} finally {
  $watcher.Dispose()
}
