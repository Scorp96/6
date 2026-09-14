$ErrorActionPreference = "Stop"

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Start-Process powershell.exe -Verb RunAs -ArgumentList $args
    exit
}

$Root = "C:\ScorpAgent"
$Relay = Join-Path $Root "scorp-agent\relay.ps1"
$TaskName = "ScorpComputerAgent"

Write-Host "=== Scorp Computer Agent installer ===" -ForegroundColor Cyan

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is not installed."
}
& gh auth status --hostname github.com | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated."
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is not installed. Install Node.js first."
}

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    Write-Host "Installing OpenAI Codex CLI..." -ForegroundColor Yellow
    & npm install -g @openai/codex
    if ($LASTEXITCODE -ne 0) { throw "Codex CLI installation failed." }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

Write-Host "Checking Codex authentication..." -ForegroundColor Yellow
& codex login status | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host "Codex needs one-time ChatGPT authentication. A browser window will open." -ForegroundColor Yellow
    & codex login
    if ($LASTEXITCODE -ne 0) { throw "Codex login failed." }
}

if (-not (Test-Path $Relay)) {
    throw "Relay script not found at $Relay. Clone the scorp-computer-relay branch into C:\ScorpAgent first."
}

try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
} catch {}

$userId = (whoami).Trim()
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Relay`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 3650)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Scorp Computer Agent GitHub relay" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

$task = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "Scorp Computer Agent installed." -ForegroundColor Green
Write-Host "Scheduled task: $($task.TaskName) / $($task.State)"
Write-Host "Relay: $Relay"
Write-Host "Repo queue: Scorp96/666"
Write-Host "The relay will now process open [SCORP_AGENT] issues automatically."
