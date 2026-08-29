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
  [switch]$SkipHealthcheck,
  [switch]$SkipRegression,
  [switch]$Strict
)

$script = Join-Path $ProjectRoot 'scripts\ci_gate.ps1'
if(!(Test-Path $script)){
  Write-Error "Missing gate script: $script"
  exit 90
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
if($SkipHealthcheck){ $invokeArgs.SkipHealthcheck = $true }
if($SkipRegression){ $invokeArgs.SkipRegression = $true }
if($Strict){ $invokeArgs.Strict = $true }

& $script @invokeArgs
exit $LASTEXITCODE
