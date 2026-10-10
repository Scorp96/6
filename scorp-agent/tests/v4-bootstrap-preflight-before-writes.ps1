param([string]$Bootstrap=(Join-Path $PSScriptRoot '..\bootstrap-v4.ps1'))
$ErrorActionPreference = 'Stop'
$path=(Resolve-Path -LiteralPath $Bootstrap).Path
$tokens=$null;$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count-ne0){throw 'P0_BOOTSTRAP_PARSER_INVALID'}
$s=[IO.File]::ReadAllText($path)
$anchors=@(
    '$preflightPassed=$false',
    '    $task=Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop',
    '    $principal=Assert-InteractivePrincipal $task',
    'if([string]$task.Principal.RunLevel-cne"Limited")',
    'P0_UNSAFE_RUNLEVEL: built-in privileged account',
    'EnableLUA -ErrorAction Stop',
    '$preflightPassed=$true',
    '    New-Item -ItemType Directory -Force -Path $StateDir,$StagingDir | Out-Null',
    '    $manifestObj=Get-PinnedContentObject $ManifestRepoPath',
    '    foreach($repoPath in $paths.Keys){$artifacts+=Install-PinnedFile',
    '    Stop-ScheduledTask -TaskName $TaskName',
    '    if($preflightPassed){',
    '    if($preflightPassed -and (Test-Path -LiteralPath $StagingDir'
)
$last=-1
foreach($needle in $anchors){
    $offset=$s.IndexOf($needle,[StringComparison]::Ordinal)
    if($offset -lt 0){throw "P0_BOOTSTRAP_ANCHOR_MISSING"}
    if($needle -eq '    if($preflightPassed){' -or $needle -like '    if($preflightPassed -and*'){
        continue
    }
    if($offset -le $last){throw 'P0_BOOTSTRAP_PRECONDITION_ORDER_INVALID'}
    $last=$offset
}
$pre=$s.IndexOf('$preflightPassed=$true',[StringComparison]::Ordinal)
$write=$s.IndexOf('    New-Item -ItemType Directory -Force -Path $StateDir,$StagingDir | Out-Null',[StringComparison]::Ordinal)
if($pre -lt 0 -or $pre -ge $write){throw 'P0_BOOTSTRAP_FIRST_MUTATION_NOT_FENCED'}
$guard=$s.IndexOf('    if($preflightPassed){',[StringComparison]::Ordinal)
$catch=$s.LastIndexOf('catch {',[StringComparison]::Ordinal)
if($guard -lt $catch){throw 'P0_BOOTSTRAP_FAILURE_REPORT_NOT_FENCED'}
$final=$s.IndexOf('    if($preflightPassed -and (Test-Path -LiteralPath $StagingDir',[StringComparison]::Ordinal)
if($final -le $guard){throw 'P0_BOOTSTRAP_FINALLY_NOT_FENCED'}
Write-Output 'P0_BOOTSTRAP_NO_PRECHECK_SIDE_EFFECTS_STATIC_PASS'
