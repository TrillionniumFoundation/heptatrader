param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$BuildDir = '',
  [string]$IbHost = '',
  [int]$Port = 0,
  [int]$ClientId = 0,
  [int]$TimeoutSec = 120,
  [int]$CancelDelaySec = 0,
  [string]$PythonExe = 'python',
  [switch]$SkipFaultInjection
)

$ErrorActionPreference = 'Stop'
if([string]::IsNullOrWhiteSpace($BuildDir)){
  $BuildDir = Join-Path $ProjectRoot 'build-agent-os-ci'
}
if(!(Test-Path (Join-Path $BuildDir 'CTestTestfile.cmake'))){
  throw "Configured CTest build directory is required: $BuildDir"
}

$runtimeRoot = Join-Path $ProjectRoot 'runtime-logs\ib-regression-round'
$roundId = Get-Date -Format 'yyyyMMdd-HHmmss'
$roundDir = Join-Path $runtimeRoot $roundId
New-Item -ItemType Directory -Path $roundDir -Force | Out-Null
$reportJson = Join-Path $roundDir 'round_report.json'
$stdoutFile = Join-Path $roundDir 'runner.stdout.log'
$stderrFile = Join-Path $roundDir 'runner.stderr.log'

$testRegex = '^hepta_(tool_gateway_runtime_composition|execution_service_process_e2e)_tests$'
$ctestArgs = @('--test-dir', $BuildDir, '--output-on-failure', '--tests-regex', $testRegex)
$process = Start-Process -FilePath 'ctest' -ArgumentList $ctestArgs -PassThru -NoNewWindow -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
if(-not $process.WaitForExit($TimeoutSec * 1000)){
  $process.Kill()
  $exitCode = 124
} else {
  $exitCode = [int]$process.ExitCode
}

$overall = if($exitCode -eq 0){ 'PASS' } else { 'FAIL' }
$report = [ordered]@{
  schema = 'hepta.offline-tool-gateway-regression.v1'
  timestamp = (Get-Date).ToUniversalTime().ToString('o')
  mode = 'offline-simulator-fake-venue'
  overall = $overall
  paper_authorized = $false
  live_authorized = $false
  broker_connection_attempted = $false
  exit_code = $exitCode
  build_dir = $BuildDir
  checks = [ordered]@{
    tool_gateway_execution = [ordered]@{ pass = ($exitCode -eq 0); detail = 'authoritative Tool Gateway/Execution Service offline tests' }
  }
  artifacts = [ordered]@{ stdout = $stdoutFile; stderr = $stderrFile }
}
$report | ConvertTo-Json -Depth 8 | Out-File -FilePath $reportJson -Encoding utf8

"ROUND_ID=$roundId`r`nOVERALL=$overall`r`nMODE=offline-simulator-fake-venue`r`nREPORT_JSON=$reportJson" |
  Out-File -FilePath (Join-Path $roundDir 'round_report.txt') -Encoding utf8

Write-Output "ROUND_ID=$roundId"
Write-Output "OVERALL=$overall"
Write-Output "REPORT_JSON=$reportJson"
exit $exitCode
