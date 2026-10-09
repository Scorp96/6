param([string]$Installer = (Join-Path $PSScriptRoot '..\install.ps1'))
$ErrorActionPreference = 'Stop'
$path = (Resolve-Path -LiteralPath $Installer).Path
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count -ne 0) { throw 'P0_FRESH_INSTALLER_PARSE_FAILED' }
$s = [IO.File]::ReadAllText($path)
$defaultDeny = $s.IndexOf('if (-not $InstallFreshLimited)',[StringComparison]::Ordinal)
$ack = $s.IndexOf("FRESH_LIMITED_ONLY_NO_EXISTING_TASK",[StringComparison]::Ordinal)
$firstExisting = $s.IndexOf('$task = Get-ScheduledTask',[StringComparison]::Ordinal)
$firstPin = $s.IndexOf('Get-FileHash -LiteralPath $Relay',[StringComparison]::Ordinal)
$firstElevate = $s.IndexOf('Start-Process powershell.exe',[StringComparison]::Ordinal)
$againExisting = $s.IndexOf('P0_FRESH_ONLY_TASK_APPEARED_DURING_ELEVATION',[StringComparison]::Ordinal)
$againSid = $s.IndexOf('P0_FRESH_INSTALL_ELEVATED_BUILTIN_ACCOUNT_REFUSED',[StringComparison]::Ordinal)
$limited = $s.IndexOf('-LogonType Interactive -RunLevel Limited',[StringComparison]::Ordinal)
$register = $s.IndexOf('Register-ScheduledTask -TaskName $TaskName',[StringComparison]::Ordinal)
$verified = $s.IndexOf('$installed.Principal.RunLevel',[StringComparison]::Ordinal)
$manual = $s.IndexOf('P0_FRESH_LIMITED_TASK_REGISTERED_DISABLED_NOT_STARTED',[StringComparison]::Ordinal)
if ($defaultDeny -lt 0 -or $ack -le $defaultDeny -or
    $firstExisting -le $ack -or $firstPin -le $firstExisting -or
    $firstElevate -le $firstPin -or $againExisting -le $firstElevate -or
    $againSid -le $firstElevate -or $limited -le $againExisting -or
    $register -le $limited -or $verified -le $register -or $manual -le $verified) {
    throw 'P0_FRESH_INSTALLER_AUTHORITY_ORDER_INVALID'
}
if ($s -notmatch 'P0_FRESH_INSTALL_UAC_REQUIRED') { throw 'P0_FRESH_INSTALLER_UAC_GUARD_MISSING' }
$disabled = $s.IndexOf('New-ScheduledTaskSettingsSet -Disable ',[StringComparison]::Ordinal)
$verifiedDisabled = $s.IndexOf('$installed.State -cne',[StringComparison]::Ordinal)
if ($disabled -lt $againExisting -or $disabled -ge $register -or $verifiedDisabled -lt $register) {
    throw 'P0_FRESH_INSTALLER_MUST_REGISTER_DISABLED'
}
if ($s -notmatch 'P0_FRESH_INSTALL_RELAY_HASH_MISMATCH') { throw 'P0_FRESH_INSTALLER_PIN_REQUIRED' }
if ($s -match '(?m)^\s*(Unregister-ScheduledTask|Stop-ScheduledTask|Start-ScheduledTask)\b') {
    throw 'P0_FRESH_INSTALLER_PRODUCTION_TASK_MUTATION_DETECTED'
}
if ($s -match '(?m)^\s*\&?\s*(?:npm|npx)\s+install\b' -or
    $s -match '(?m)^\s*\&?\s*codex(?:\.exe)?\s+login\s*$') {
    throw 'P0_FRESH_INSTALLER_AUTO_EXTERNAL_SETUP_DETECTED'
}
$registrationLine = ($s.Split([char]10) | Where-Object { $_ -match '^\s*Register-ScheduledTask\b' })
if (@($registrationLine).Count -ne 1 -or [string]$registrationLine -match '\-Force\b') {
    throw 'P0_FRESH_INSTALLER_TASK_OVERWRITE_ENABLED'
}
Write-Output 'P0_FRESH_INSTALLER_NO_EXISTING_TASK_NO_ELEVATED_AUTOSTART_PASS'
