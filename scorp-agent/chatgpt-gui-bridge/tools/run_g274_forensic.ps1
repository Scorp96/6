$ErrorActionPreference='Stop'
$expected='c14c6f90ac259ce44105b3b74a69f41f68c436c1'
$base='C:\ScorpAgent\worktrees\multi-agent-relay-v2'
$wt='C:\ScorpAgent\worktrees\master-worker-v3'
git -C $base fetch origin scorp-master-worker-v3
if($LASTEXITCODE-ne0){throw 'FETCH_FAILED'}
$remote=(git -C $base rev-parse FETCH_HEAD).Trim()
if($remote-ne$expected){throw ('REMOTE_HEAD_CHANGED:'+ $remote)}
git -C $wt reset --hard $expected
if($LASTEXITCODE-ne0){throw 'RESET_FAILED'}
$tool=Join-Path $wt 'scorp-agent\chatgpt-gui-bridge\tools\production_active_root_forensic_v3.py'
$py='C:\ScorpAgent\chatgpt-gui-bridge-runtime\Scripts\python.exe'
& $py $tool
if($LASTEXITCODE-ne0){throw ('FORENSIC_FAILED:'+ $LASTEXITCODE)}
