$ErrorActionPreference = "Stop"
$relay = Join-Path $PSScriptRoot "relay.ps1"
$mutexName = "Local\ScorpComputerAgentRelay-selftest-$([guid]::NewGuid().ToString('N'))"

& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -NoProfile `
    -NonInteractive `
    -ExecutionPolicy Bypass `
    -File $relay `
    -SelfTest `
    -MutexName $mutexName

exit $LASTEXITCODE
