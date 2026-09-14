Set-StrictMode -Version 2.0

function Get-ControlState {
    param([Parameter(Mandatory=$true)][string]$Root)
    $path=Join-Path $Root 'control.json'
    if(-not(Test-Path -LiteralPath $path -PathType Leaf)){
        return [pscustomobject]@{protocol_version='scorp.orchestrator/control-v1';paused=$false;emergency_stop=$false;updated_at=$null}
    }
    try{$c=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json}catch{throw "CONTROL_INVALID_JSON $($_.Exception.Message)"}
    Assert-RequiredProperties $c @('protocol_version','paused','emergency_stop') 'CONTROL'
    if([string]$c.protocol_version-cne'scorp.orchestrator/control-v1'){throw "CONTROL_PROTOCOL_MISMATCH actual=$($c.protocol_version)"}
    return $c
}

function Set-HealthState {
    param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)]$Health)
    Assert-RequiredProperties $Health @('protocol_version','status','heartbeat_at') 'HEALTH'
    if([string]$Health.protocol_version-cne'scorp.orchestrator/health-v1'){throw "HEALTH_PROTOCOL_MISMATCH actual=$($Health.protocol_version)"}
    if(-not(Test-Path -LiteralPath $Root -PathType Container)){New-Item -ItemType Directory -Path $Root -Force|Out-Null}
    Write-Utf8NoBomAtomic -Path (Join-Path $Root 'health.json') -Text ($Health|ConvertTo-Json -Depth 32)
    return $Health
}
