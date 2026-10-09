param(
  [ValidateSet(15,25)][int]$IntervalMinutes = 15,
  [string]$ExpectedCommit = 'c0eafd28c0406259be813759babece4cf5b1764a'
)
$ErrorActionPreference = 'Stop'

# This runner has no browser send, task creation or restart capability.
# It may ONLY invoke the model-free, no-send preflight observer in the
# pinned isolated checkout. All state changes go to the experiments folder.
$repo = 'C:\ScorpAgent\experiments\r2-gpt-session-audit-20261009'
$root = 'C:\ScorpAgent\experiments'
$python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe'
$chrome = 'C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe'
$workspace = if ($IntervalMinutes -eq 15) {
  Join-Path $root 'r2-observer-state-20261009'
} else {
  Join-Path $root 'r2-observer-state-25m-20261009'
}
$taskName = 'ScorpR2GPTObserver' + $IntervalMinutes + 'mCanary'

function Assert-ExistingFile([string]$Path, [string]$Reason) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Reason }
}

if ($ExpectedCommit -cnotmatch '^[a-f0-9]{40}$') { throw 'INVALID_EXPECTED_SHA' }
Assert-ExistingFile $python 'PYTHON_NOT_FOUND'
Assert-ExistingFile $chrome 'CHROME_USE_NOT_FOUND'
Assert-ExistingFile (Join-Path $repo '.git\HEAD') 'ISOLATED_CHECKOUT_NOT_FOUND'
Assert-ExistingFile (Join-Path $repo 'scorp-agent\master_a_dynamic_v4\local_tick_observer.py') 'OBSERVER_MODULE_MISSING'
if (-not (Test-Path -LiteralPath $workspace -PathType Container)) { throw 'OBSERVER_STATE_NOT_FOUND' }
$actual = (& git -C $repo rev-parse HEAD 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $actual -cne $ExpectedCommit) { throw 'PINNED_SHA_MISMATCH' }
# Git cleanliness is checked before invoking any module; it prevents
# an edited Python file from evading the commit SHA pin.
$dirty = (& git -C $repo status --porcelain 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'ISOLATED_CHECKOUT_DIRTY' }
$env:PYTHONPATH = Join-Path $repo 'scorp-agent'
$args = @(
  '-B', '-m', 'master_a_dynamic_v4.local_tick_observer',
  '--workspace', $workspace,
  '--interval-minutes', [string]$IntervalMinutes,
  '--v3-project-root', 'C:\ScorpAgent\state-v3\active',
  '--gui-health-file', 'C:\ScorpAgent\chatgpt-gui-bridge-state\health.json',
  '--r1-state-db', 'C:\ScorpAgent\runtime-v4\active\state.sqlite3',
  '--chrome-use-executable', $chrome
)
$line = (& $python @args | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'OBSERVER_ONE_SHOT_FAILED' }
try { $answer = $line | ConvertFrom-Json } catch { throw 'OBSERVER_RESULT_UNPARSEABLE' }
if ([bool]$answer.browser_send_authorized -or [bool]$answer.browser_adoption_authorized) {
  throw 'OBSERVER_VIOLATED_NO_SEND'
}
if ([string]$answer.status -notin @('OBSERVED','SKIPPED','BLOCKED')) { throw 'OBSERVER_INVALID_STATUS' }
@{
  protocol_version = 'scorp.isolated-observer-task/1'
  task_name = $taskName
  interval_minutes = $IntervalMinutes
  result_status = [string]$answer.status
  result_reason = [string]$answer.reason
  event_seq = $answer.event_seq
  model_calls = 0
  browser_send_authorized = $false
} | ConvertTo-Json -Compress
