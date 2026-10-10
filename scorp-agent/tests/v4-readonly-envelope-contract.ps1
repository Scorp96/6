param(
  [string]$RunnerPath = (Join-Path $PSScriptRoot '..\runner-v4.1.ps1'),
  [string]$ExecutorPath = (Join-Path $PSScriptRoot '..\executor-v4.1.ps1')
)
$ErrorActionPreference='Stop'
$base=Join-Path $env:TEMP ('scorp-p0-readonly-'+[guid]::NewGuid().ToString('N'))
New-Item -Path $base -ItemType Directory -Force|Out-Null
$cases=0
function Assert-Case([bool]$Condition,[string]$Message){
  if(-not $Condition){throw "P0_READONLY_CASE_FAILED: $Message"}
}
function Invoke-Case([string]$Kind,$Claims,$Payload,[int]$ExpectedExit,[string]$Status,[string]$Auth=''){
  $script:cases++
  $id='p0-action-'+$script:cases.ToString('000')
  $envelope=Join-Path $base "$id.json"
  $resultPath=Join-Path $base "$id.result.json"
  $logPath=Join-Path $base "$id.log"
  $obj=[ordered]@{
    protocol_version='scorp.exec/v4'; task_id=$id; action_id='p0-action-00000001'
    action_kind=$Kind; timeout_seconds=20
    safety_class=$(if($Kind-eq'privileged_broker'){'approved_admin'}else{'standard'})
    authorization=$Auth; expected_preconditions=$Claims; payload=$Payload
  }
  [IO.File]::WriteAllText($envelope,($obj|ConvertTo-Json -Depth 12),(New-Object Text.UTF8Encoding($false)))
  $nativeArgs=@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$RunnerPath,
    '-EnvelopePath',$envelope,'-ResultPath',$resultPath,'-LogPath',$logPath)
  & powershell.exe @nativeArgs 2>&1|Out-Null
  $code=$LASTEXITCODE
  Assert-Case ($code-eq$ExpectedExit) "$id exit=$code expected=$ExpectedExit"
  Assert-Case (Test-Path -LiteralPath $resultPath -PathType Leaf) "$id missing result"
  $res=Get-Content -LiteralPath $resultPath -Raw|ConvertFrom-Json
  Assert-Case ([string]$res.status-ceq$Status) "$id status=$($res.status)"
  return $res
}
try {
  $marker=Join-Path $base 'shell-marker.txt'
  $marker2=Join-Path $base 'process-marker.txt'
  $writePath=Join-Path $base 'write-marker.txt'
  $readPath=Join-Path $base 'read.txt'
  [IO.File]::WriteAllText($readPath,'read-only fixture')
  $shell=@{script="Set-Content -LiteralPath '$marker' -Value unsafe"}
  $r=Invoke-Case 'powershell' @{read_only=$true;no_production_writes=$true} $shell 3 'PRECONDITION_FAILED'
  Assert-Case ($r.message -match 'not enforceable') 'shell error explanation'
  Assert-Case (-not(Test-Path $marker)) 'shell executed'
  $proc=@{executable='powershell.exe';argv=@('-NoProfile','-Command',"Set-Content -LiteralPath '$marker2' -Value unsafe")}
  Invoke-Case 'process' @{no_process_kill=$true} $proc 3 'PRECONDITION_FAILED'|Out-Null
  Assert-Case (-not(Test-Path $marker2)) 'process executed'
  Invoke-Case 'git' @{no_browser_submit=$true} @{argv=@('status')} 3 'PRECONDITION_FAILED'|Out-Null
  Invoke-Case 'powershell' @{read_only='true'} $shell 3 'PRECONDITION_FAILED'|Out-Null
  Invoke-Case 'file_write' @{read_only=$true} @{path=$writePath;content='unsafe'} 3 'PRECONDITION_FAILED'|Out-Null
  Assert-Case (-not(Test-Path $writePath)) 'file_write executed'
  Invoke-Case 'file_replace_exact' @{no_production_writes=$true} @{path=$readPath;old_text='read';new_text='unsafe'} 3 'PRECONDITION_FAILED'|Out-Null
  Assert-Case ([IO.File]::ReadAllText($readPath)-ceq'read-only fixture') 'file_replace executed'
  Invoke-Case 'privileged_broker' @{read_only=$true} @{operation='task.run';params=@{name='ScorpComputerAgent'}} 3 'PRECONDITION_FAILED' 'USER_APPROVED_FULL_CONTROL'|Out-Null
  $read=Invoke-Case 'file_read' @{read_only=$true;no_production_writes=$true} @{path=$readPath;max_bytes=1024} 0 'SUCCEEDED'
  Assert-Case ([string]$read.evidence.text-ceq'read-only fixture') 'file_read broken'
  Invoke-Case 'health' @{read_only=$true} @{} 0 'SUCCEEDED'|Out-Null
  foreach($path in @($ExecutorPath,$RunnerPath)){
    $tokens=$null;$errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $path),[ref]$tokens,[ref]$errors)
    Assert-Case ($errors.Count-eq0) "parse error $path"
    $defs=@($ast.FindAll({
      param($node)
      $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name-ceq'Assert-ExpectedPreconditionContract'
    },$true))
    Assert-Case ($defs.Count-eq1) "helper absent $path"
  }
  $parent=[IO.File]::ReadAllText((Resolve-Path $ExecutorPath).Path)
  $validator=$parent.IndexOf('function Validate-Envelope',[StringComparison]::Ordinal)
  $gate=$parent.IndexOf('Assert-ExpectedPreconditionContract -Envelope $Envelope',$validator,[StringComparison]::Ordinal)
  $reservation=$parent.IndexOf('function Reserve-ActionIdentity',[StringComparison]::Ordinal)
  Assert-Case ($validator-ge0 -and $gate-gt$validator -and $gate-lt$reservation) 'parent admission order'
  Write-Output ("P0_READONLY_ENVELOPE_CONTRACT_PASS cases={0}" -f $cases)
} finally {
  Remove-Item -LiteralPath $base -Force -Recurse -ErrorAction SilentlyContinue
}
