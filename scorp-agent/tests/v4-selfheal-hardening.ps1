param(
    [string]$BootstrapPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "bootstrap-v4.ps1")
)

$ErrorActionPreference = "Stop"
function Assert-True { param([bool]$Condition,[string]$Name); if(-not $Condition){throw "V4_SELFHEAL_REGRESSION_FAIL: $Name"}; Write-Output "PASS $Name" }

$bootstrap=Get-Content -LiteralPath $BootstrapPath -Raw

# The executor must recover after process exit/reboot without requiring an external control hand.
Assert-True ($bootstrap.Contains('New-ScheduledTaskPrincipal -UserId $principal.user_name -LogonType Interactive')) "normalized-interactive-principal"
Assert-True ($bootstrap.Contains('New-ScheduledTaskTrigger -AtLogOn -User $principal.user_name')) "logon-trigger-uses-resolved-identity"
Assert-True ($bootstrap.Contains('New-ScheduledTaskTrigger -AtStartup')) "startup-trigger"
Assert-True ($bootstrap.Contains('-RepetitionInterval (New-TimeSpan -Minutes 1)')) "minute-repetition-trigger"
Assert-True ($bootstrap.Contains('-StartWhenAvailable') -and $bootstrap.Contains('-MultipleInstances IgnoreNew')) "selfheal-task-settings"
Assert-True ($bootstrap.Contains('-RestartCount 3') -and $bootstrap.Contains('-RestartInterval (New-TimeSpan -Minutes 1)')) "bounded-task-restart"
Assert-True ($bootstrap.Contains('-Trigger @($logonTrigger,$startupTrigger,$repeatTrigger)') -and $bootstrap.Contains('-Settings $runtimeSettings') -and $bootstrap.Contains('-Principal $normalizedPrincipal')) "selfheal-applied-to-main-task"
Assert-True ($bootstrap.Contains('MSFT_TaskLogonTrigger') -and $bootstrap.Contains('MSFT_TaskBootTrigger') -and $bootstrap.Contains('MSFT_TaskTimeTrigger')) "post-switch-trigger-verification"
Assert-True ($bootstrap.Contains('MultipleInstances') -and $bootstrap.Contains('RestartCount')) "post-switch-setting-verification"
Assert-True (-not ($bootstrap -match '(?i)codex\s+exec|codex\.exe')) "bootstrap-no-codex-invocation"

Write-Output "V4_SELFHEAL_HARDENING_PASS"
