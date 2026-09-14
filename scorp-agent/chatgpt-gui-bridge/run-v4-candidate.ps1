param(
    [string]$ProjectId = 'scorp-v4-candidate',
    [string]$DatabasePath = '',
    [string]$AllowedRoot = ''
)

$ErrorActionPreference = 'Stop'
$BridgeRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $BridgeRoot '..\..')).Path
$Python = if ($env:SCORP_PYTHON) { $env:SCORP_PYTHON } else { 'python' }
if (-not $DatabasePath) { $DatabasePath = Join-Path $RepoRoot 'state\v4-candidate.sqlite3' }
if (-not $AllowedRoot) { $AllowedRoot = $RepoRoot }

if (-not (Test-Path -LiteralPath $BridgeRoot -PathType Container)) { throw 'V4_BRIDGE_ROOT_MISSING' }
if ($Python -ne 'python' -and -not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'V4_PYTHON_MISSING' }
if (-not (Test-Path -LiteralPath $AllowedRoot -PathType Container)) { throw 'V4_ALLOWED_ROOT_MISSING' }

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
& $Python (Join-Path $BridgeRoot 'v4_runtime.py') `
    --describe `
    --database-path $DatabasePath `
    --project-id $ProjectId `
    --allowed-root $AllowedRoot
exit $LASTEXITCODE
