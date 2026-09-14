[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$CandidateCommit,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$LabRoot = 'C:\ScorpAgent\v4-core-lab',
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$manifest = (Resolve-Path -LiteralPath $ManifestPath).Path
$moduleRoot = Join-Path $source 'scorp-agent'
$priorPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $moduleRoot
    & $Python -B -m master_a_dynamic_v4.install_manifest `
        --source $source `
        --target $LabRoot `
        --manifest $manifest `
        --candidate $CandidateCommit `
        --interpreter $Python `
        --protected 'C:\ScorpAgent\state-v3\active' `
        --protected 'C:\ScorpAgent\state-v4' `
        --protected 'C:\ScorpAgent\gpt-native-v4' `
        --protected 'C:\ScorpAgent\chatgpt-gui-bridge-runtime'
    if ($LASTEXITCODE -ne 0) {
        throw "SCORP V4 lab installation failed with exit code $LASTEXITCODE"
    }
}
finally {
    $env:PYTHONPATH = $priorPythonPath
}
