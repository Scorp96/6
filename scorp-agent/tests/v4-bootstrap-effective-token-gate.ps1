param([string]$Bootstrap=(Join-Path $PSScriptRoot '..\bootstrap-v4.ps1'))
$ErrorActionPreference='Stop'
$path=(Resolve-Path -LiteralPath $Bootstrap).Path
$tokens=$null;$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count -ne 0){throw 'P0_EFFECTIVE_TOKEN_BOOTSTRAP_PARSER_INVALID'}
$s=[IO.File]::ReadAllText($path)
$markers=@(
    '    $task=Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop',
    '    $taskIdentity=[Security.Principal.WindowsIdentity]::GetCurrent()',
    'P0_UNSAFE_RUNLEVEL: built-in privileged account',
    'EnableLUA -ErrorAction Stop',
    '$effectiveTokenPrincipal=New-Object Security.Principal.WindowsPrincipal($taskIdentity)',
    '$effectiveTokenPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)',
    'P0_UNSAFE_EFFECTIVE_TOKEN: elevated administrator process cannot bootstrap Limited task',
    '$preflightPassed=$true',
    '    New-Item -ItemType Directory -Force -Path $StateDir,$StagingDir | Out-Null'
)
$prior=-1
foreach($m in $markers){
    $pos=$s.IndexOf($m,[StringComparison]::Ordinal)
    if($pos-lt0){throw 'P0_EFFECTIVE_TOKEN_GUARD_MISSING'}
    if($pos -le $prior){throw 'P0_EFFECTIVE_TOKEN_CHECK_AFTER_FIRST_WRITE'}
    $prior=$pos
}
if($s -match '(?s)\$preflightPassed=\$true.*P0_UNSAFE_EFFECTIVE_TOKEN'){
    throw 'P0_EFFECTIVE_TOKEN_APPROVAL_AFTER_STAGING'
}
Write-Output 'P0_BOOTSTRAP_EFFECTIVE_ADMIN_TOKEN_DENIED_BEFORE_FIRST_WRITE_STATIC_PASS'
