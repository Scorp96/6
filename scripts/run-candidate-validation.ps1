$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$agentRoot = Join-Path $repo 'scorp-agent'
$bridgeRoot = Join-Path $agentRoot 'chatgpt-gui-bridge'
$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $agentRoot
    Write-Host '== V4 tests =='
    & python -B -m unittest discover -s (Join-Path $agentRoot 'master_a_dynamic_v4/tests') -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw "V4_TESTS_FAILED:$LASTEXITCODE" }

    Write-Host '== GUI bridge tests =='
    Push-Location $bridgeRoot
    try {
        & python -B -m unittest discover -s tests -p 'test_*.py'
        if ($LASTEXITCODE -ne 0) { throw "BRIDGE_TESTS_FAILED:$LASTEXITCODE" }
    }
    finally { Pop-Location }

    Write-Host '== Python compilation =='
    & python -B -m compileall -q (Join-Path $agentRoot 'master_a_dynamic_v4') $bridgeRoot
    if ($LASTEXITCODE -ne 0) { throw "COMPILE_FAILED:$LASTEXITCODE" }
    Write-Host 'CANDIDATE_VALIDATION=PASS'
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}
