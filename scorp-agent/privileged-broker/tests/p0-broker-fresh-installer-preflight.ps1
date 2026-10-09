param([string]$Installer=(Join-Path $PSScriptRoot '..\install-broker.ps1'))
$ErrorActionPreference='Stop'
$source=(Resolve-Path -LiteralPath $Installer).Path
$tokens=$null;$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count-ne0){throw 'P0_BROKER_INSTALL_PARSE_INVALID'}
$text=[IO.File]::ReadAllText($source)
$required=@(
    'if (-not $InstallFreshIsolated)',
    'P0_BROKER_INSTALL_DEFAULT_DENY_USE_READONLY_AUDIT',
    "FRESH_ISOLATED_BROKER_NO_PRIOR_STATE",
    '$existingService = Get-Service',
    'P0_BROKER_INSTALL_EXISTING_SERVICE_OR_STATE_REFUSED',
    '$pinned = [ordered]@{',
    'P0_BROKER_INSTALL_ALL_FOUR_SOURCE_SHA256_REQUIRED',
    'Get-FileHash -LiteralPath $source -Algorithm SHA256',
    'P0_BROKER_INSTALL_SOURCE_SHA256_MISMATCH',
    "New-Item -ItemType Directory -Path $backupRoot -Force"
)
$previous=-1
foreach($k in $required){
    $position=$text.IndexOf($k,[StringComparison]::Ordinal)
    if($position-lt0){throw ('P0_BROKER_PRECHECK_MARKER_MISSING: '+$k)}
    if($position-lt$previous){throw 'P0_BROKER_PRECHECK_MARKER_OUT_OF_ORDER'}
    $previous=$position
}
$firstWrite=$text.IndexOf('New-Item -ItemType Directory -Path $backupRoot',[StringComparison]::Ordinal)
$sourceHash=$text.IndexOf('P0_BROKER_INSTALL_SOURCE_SHA256_MISMATCH',[StringComparison]::Ordinal)
if($firstWrite-lt0 -or $firstWrite-le$sourceHash){
    throw 'P0_BROKER_INSTALL_FIRST_WRITE_BEFORE_AUTHORITY'
}
$invocations=@(
    @{arguments=''; reason='P0_BROKER_INSTALL_DEFAULT_DENY_USE_READONLY_AUDIT'},
    @{arguments='-InstallFreshIsolated'; reason='P0_BROKER_INSTALL_EXPLICIT_HUMAN_REVIEW_REQUIRED'}
)
$winPs=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$rootBefore=Test-Path 'C:\ScorpAgent'
foreach($t in $invocations){
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$winPs
    $psi.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$source+'" '+$t.arguments
    $psi.UseShellExecute=$false
    $psi.RedirectStandardOutput=$true
    $psi.RedirectStandardError=$true
    $psi.CreateNoWindow=$true
    $p=[Diagnostics.Process]::Start($psi)
    try {
        if(-not $p.WaitForExit(10000)){
            try{$p.Kill()}catch{}
            throw 'P0_BROKER_INSTALL_DENIAL_TIMEOUT'
        }
        $out=$p.StandardOutput.ReadToEnd()+$p.StandardError.ReadToEnd()
        if($p.ExitCode-eq0 -or -not $out.Contains($t.reason)){
            throw 'P0_BROKER_INSTALL_DENIAL_NOT_PROVEN'
        }
    }finally{$p.Dispose()}
}
if(-not$rootBefore -and (Test-Path 'C:\ScorpAgent')){
    throw 'P0_BROKER_INSTALL_DENIAL_CREATED_ROOT'
}
Write-Output 'P0_BROKER_INSTALL_FRESH_ONLY_DENIAL_NO_WRITE_PASS'
