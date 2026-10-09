param([string]$RunnerPath=(Join-Path $PSScriptRoot '..\runner-v4.1.ps1'))
$ErrorActionPreference='Stop'
$root=Join-Path $env:TEMP ('scorp-typed-probe-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root -Force|Out-Null
$case=0
function Assert([bool]$Ok,[string]$Why){if(-not$Ok){throw "P0_TYPED_PROBE_FAILED $Why"}}
function Invoke-Probe($Payload,[int]$ExpectedCode,[string]$ExpectedStatus){
    $script:case++
    $id='probe-'+$script:case
    $req=Join-Path $root "$id.json"
    $res=Join-Path $root "$id.result.json"
    $log=Join-Path $root "$id.log"
    $data=[ordered]@{
        protocol_version='scorp.exec/v4';task_id=$id;action_id='probe-action-00000001'
        action_kind='diagnostic_readonly';timeout_seconds=20;safety_class='standard'
        expected_preconditions=@{read_only=$true;no_production_writes=$true;no_browser_submit=$true}
        payload=$Payload
    }
    [IO.File]::WriteAllText($req,($data|ConvertTo-Json -Depth 12),(New-Object Text.UTF8Encoding($false)))
    $nativeArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$RunnerPath,
        '-EnvelopePath',$req,'-ResultPath',$res,'-LogPath',$log)
    & powershell.exe @nativeArgs 2>&1|Out-Null
    Assert ($LASTEXITCODE-eq$ExpectedCode) "$id code=$LASTEXITCODE"
    Assert (Test-Path -LiteralPath $res -PathType Leaf) "$id no result"
    $out=Get-Content -LiteralPath $res -Raw|ConvertFrom-Json
    Assert ($out.status-ceq$ExpectedStatus) "$id status=$($out.status)"
    return $out
}
try{
    $ok=Invoke-Probe @{probe='chrome_resource_summary'} 0 'SUCCEEDED'
    Assert ($ok.evidence.diagnostic.probe-ceq'chrome_resource_summary') 'result identity'
    Assert ([int]$ok.evidence.diagnostic.process_count-ge0) 'process count'
    Assert ([int64]$ok.evidence.diagnostic.working_set_bytes-ge0) 'memory'
    Assert ($ok.evidence.diagnostic.process_owner_attested-eq$false) 'false ownership attestation'
    $marker=Join-Path $root 'pwn.txt'
    $bad=Invoke-Probe @{probe='chrome_resource_summary';script="Set-Content -LiteralPath '$marker' -Value unsafe"} 3 'PRECONDITION_FAILED'
    Assert (-not(Test-Path -LiteralPath $marker)) 'script injection executed'
    $bad=Invoke-Probe @{probe='fake_process_command'} 3 'PRECONDITION_FAILED'
    Assert ($bad.message-match'unsupported diagnostic probe') 'unknown probe accepted'
    $bad=Invoke-Probe @{probe='chrome_resource_summary';path='C:\Windows\System32\config'} 3 'PRECONDITION_FAILED'
    Assert ($bad.message-match'only probe') 'path injection accepted'
    Write-Host ("P0_TYPED_READONLY_DIAGNOSTICS_PASS cases={0}" -f $case)
}finally{
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
