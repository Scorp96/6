param(
    [Parameter(Mandatory=$true)][string]$StorePath,
    [Parameter(Mandatory=$true)][string]$RouterPath,
    [Parameter(Mandatory=$true)][string]$EntrypointPath
)
$ErrorActionPreference='Stop'
function Assert-True {param([bool]$Condition,[string]$Name)if(-not$Condition){throw "ORCHESTRATOR_V1_PRODUCTION_WIRING_FAIL: $Name"};Write-Output "PASS $Name"}
. $StorePath
. $RouterPath
. $EntrypointPath -LibraryMode
Assert-True ($null-ne(Get-Command New-GitHubCliAdapter -CommandType Function -ErrorAction SilentlyContinue)) 'github-cli-adapter-defined'
Assert-True ($null-ne(Get-Command Invoke-OrchestratorTickSafe -CommandType Function -ErrorAction SilentlyContinue)) 'safe-tick-wrapper-defined'
$adapter=New-GitHubCliAdapter -RepoFullName 'Scorp96/666'
foreach($name in @('CreateIssue','SearchIssues','ReadIssue','ReadComments','UpdateIssue')){
    Assert-True ($null-ne$adapter.$name -and $adapter.$name-is[scriptblock]) ("github-cli-adapter-"+$name)
}
$router=Get-Content -LiteralPath $RouterPath -Raw
$ghStart=$router.IndexOf('function Invoke-OrchestratorGhJson',[StringComparison]::Ordinal)
$ghEnd=$router.IndexOf('function New-GitHubCliAdapter',$ghStart,[StringComparison]::Ordinal)
Assert-True ($ghStart-ge0 -and $ghEnd-gt$ghStart) 'github-transport-slice'
$gh=$router.Substring($ghStart,$ghEnd-$ghStart)
Assert-True ($gh.Contains('System.Diagnostics.ProcessStartInfo') -and $gh.Contains('RedirectStandardOutput') -and $gh.Contains('RedirectStandardError')) 'github-native-stdio-isolated'
Assert-True ($gh.Contains('WaitForExit(') -and $gh.Contains('Kill()') -and $gh.Contains('GITHUB_API_TIMEOUT')) 'github-hard-timeout-fails-closed'
Assert-True (-not$gh.Contains('& $gh @args')) 'github-no-unbounded-native-call'
$entry=Get-Content -LiteralPath $EntrypointPath -Raw
Assert-True ($entry -match 'New-GitHubCliAdapter') 'entrypoint-constructs-github-adapter'
Assert-True ($entry -match 'TrustedActor') 'entrypoint-binds-trusted-actor'
Assert-True ($entry -match 'GitHub=') 'entrypoint-injects-github-context'
Assert-True ($entry -match 'Invoke-OrchestratorTickSafe') 'entrypoint-uses-safe-tick-wrapper'
Assert-True ($entry -match "status='DEGRADED'" -and $entry -match 'tick_error') 'tick-error-degrades-not-exits'
Assert-True ($entry -notmatch '(?i)codex\s+exec|codex\.exe' -and $router -notmatch '(?i)codex\s+exec|codex\.exe') 'production-wiring-no-codex'
Write-Output 'ORCHESTRATOR_V1_PRODUCTION_WIRING_PASS'
