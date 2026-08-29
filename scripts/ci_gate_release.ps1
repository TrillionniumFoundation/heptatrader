param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$ConfigPath,
  [ValidateSet('sim','paper','live')]
  [string]$Profile,
  [string]$PythonExe = 'python',
  [string]$IbHost = '127.0.0.1',
  [int]$Port = 4002,
  [int]$HealthTimeoutSec = 45,
  [int]$RegressionTimeoutSec = 90,
  [switch]$NoLaunch,
  [switch]$Strict,
  [switch]$SkipHealthcheck,
  [switch]$SkipRegression,
  [switch]$PolicyCheckOnly,
  [string]$GateSummaryPath
)

$ErrorActionPreference = 'Stop'

function Fail-AndExit {
  param(
    [int]$Code,
    [string]$Message
  )
  Write-Host $Message -ForegroundColor Red
  exit $Code
}

$ExitCodes = [ordered]@{
  PASS = 0
  BUILD_FAIL = 10
  WHITELIST_FAIL = 11
  REGRESSION_FAIL = 12
  HEALTHCHECK_FAIL = 13
  FORBIDDEN_SKIP_OPTION = 14
  REAL_REGRESSION_REQUIRED = 15
  RECONCILE_CRITICAL_OR_MISSING = 16
  SCRIPT_MISSING = 90
  UNEXPECTED_ERROR = 99
}

function Get-GateSummaryPathFromOutput {
  param([object[]]$OutputLines)

  if($null -eq $OutputLines){ return $null }
  $line = $OutputLines |
    ForEach-Object { "$_" } |
    Where-Object { $_ -like 'SUMMARY_JSON=*' } |
    Select-Object -Last 1
  if([string]::IsNullOrWhiteSpace($line)){ return $null }
  return $line.Substring('SUMMARY_JSON='.Length).Trim()
}

function Resolve-LatestGateSummary {
  param([string]$Root)

  $runtimeRoot = Join-Path $Root 'runtime-logs'
  if(!(Test-Path $runtimeRoot)){ return $null }

  $latest = Get-ChildItem -Path $runtimeRoot -Directory -Filter 'ci-gate-*' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if($null -eq $latest){ return $null }

  $candidate = Join-Path $latest.FullName 'ci_gate_summary.json'
  if(Test-Path $candidate){ return $candidate }
  return $null
}

function Get-Check($summary, [string]$name){
  return @($summary.checks | Where-Object { $_.name -eq $name } | Select-Object -First 1)
}

function Assert-ReleasePolicy {
  param(
    [string]$SummaryPath
  )

  if([string]::IsNullOrWhiteSpace($SummaryPath) -or !(Test-Path $SummaryPath)){
    Fail-AndExit -Code $ExitCodes.REAL_REGRESSION_REQUIRED -Message "Release policy check failed: missing ci_gate summary json. path='$SummaryPath'"
  }

  $summary = Get-Content -LiteralPath $SummaryPath -Raw | ConvertFrom-Json

  $requiredChecks = @('BUILD','IB_HEALTHCHECK','IB_REGRESSION_ROUND','CTP_REGRESSION_ROUND','RECONCILE_CRITICAL_BLOCK')
  foreach($checkName in $requiredChecks){
    $c = Get-Check -summary $summary -name $checkName
    if($null -eq $c){
      Fail-AndExit -Code $ExitCodes.REAL_REGRESSION_REQUIRED -Message "Release policy check failed: missing check '$checkName' in $SummaryPath"
    }
    if(-not [bool]$c.pass){
      $code = if($checkName -eq 'RECONCILE_CRITICAL_BLOCK'){ $ExitCodes.RECONCILE_CRITICAL_OR_MISSING } elseif($checkName -eq 'BUILD'){ $ExitCodes.BUILD_FAIL } elseif($checkName -eq 'IB_HEALTHCHECK'){ $ExitCodes.HEALTHCHECK_FAIL } else { $ExitCodes.REGRESSION_FAIL }
      Fail-AndExit -Code $code -Message "Release policy check failed: $checkName pass=false detail=$($c.detail)"
    }
    if(("$($c.detail)") -match 'skipped'){
      Fail-AndExit -Code $ExitCodes.REAL_REGRESSION_REQUIRED -Message "Release policy check failed: $checkName cannot be skipped in release gate."
    }
  }

  Write-Host 'RELEASE_POLICY=PASS'
  Write-Host 'RELEASE_POLICY_SCOPE=IB+CTP_ONLY (XT excluded)'
  Write-Host "RELEASE_POLICY_SUMMARY_JSON=$SummaryPath"
}

if($SkipHealthcheck -or $SkipRegression){
  Fail-AndExit -Code $ExitCodes.FORBIDDEN_SKIP_OPTION -Message 'ci_gate_release.ps1 forbids -SkipHealthcheck / -SkipRegression. Real gate must execute build + healthcheck + IB/CTP regression + reconcile check.'
}

if($PolicyCheckOnly){
  Assert-ReleasePolicy -SummaryPath $GateSummaryPath
  exit $ExitCodes.PASS
}

$script = Join-Path $ProjectRoot 'scripts\ci_gate.ps1'
if(!(Test-Path $script)){
  Fail-AndExit -Code $ExitCodes.SCRIPT_MISSING -Message "Missing gate script: $script"
}

$invokeArgs = @{
  ProjectRoot = $ProjectRoot
  PythonExe = $PythonExe
  IbHost = $IbHost
  Port = $Port
  HealthTimeoutSec = $HealthTimeoutSec
  RegressionTimeoutSec = $RegressionTimeoutSec
}
if(-not [string]::IsNullOrWhiteSpace($ConfigPath)){ $invokeArgs.ConfigPath = $ConfigPath }
if(-not [string]::IsNullOrWhiteSpace($Profile)){ $invokeArgs.Profile = $Profile }
if($NoLaunch){ $invokeArgs.NoLaunch = $true }
if($Strict){ $invokeArgs.Strict = $true }

$gateOutput = & $script @invokeArgs
$gateExit = $LASTEXITCODE

if($gateExit -ne 0){
  exit $gateExit
}

$resolvedSummaryPath = if(-not [string]::IsNullOrWhiteSpace($GateSummaryPath)){
  $GateSummaryPath
} else {
  $fromOutput = Get-GateSummaryPathFromOutput -OutputLines $gateOutput
  if(-not [string]::IsNullOrWhiteSpace($fromOutput)){ $fromOutput } else { Resolve-LatestGateSummary -Root $ProjectRoot }
}

Assert-ReleasePolicy -SummaryPath $resolvedSummaryPath
exit $ExitCodes.PASS
