$ErrorActionPreference = 'Stop'
$Python = 'C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe'
$BridgeDir = 'C:\ScorpAgent\chatgpt-gui-bridge'
$BridgeState = 'C:\ScorpAgent\chatgpt-gui-bridge-state'
$OrchestratorRoot = 'C:\ScorpAgent\orchestrator-v1'
$V4State = 'C:\ScorpAgent\state-v4'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'BRIDGE_RUNTIME_PYTHON_MISSING' }
if (-not (Test-Path -LiteralPath "$BridgeDir\bridge_worker.py" -PathType Leaf)) { throw 'BRIDGE_WORKER_MISSING' }
New-Item -ItemType Directory -Path $BridgeState -Force | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:ANONYMIZED_TELEMETRY = 'false'

& $Python "$BridgeDir\bridge_worker.py" `
  --orchestrator-root $OrchestratorRoot `
  --state-root $V4State `
  --bridge-root $BridgeState `
  --control-repo 'Scorp96/scorp-control-plane' `
  --trusted-actor 'Scorp96' `
  --poll-seconds 10
exit $LASTEXITCODE