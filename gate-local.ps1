param(
  [string]$ProjectRoot = $PSScriptRoot,
  [string]$PythonExe = 'python',
  [string]$IbHost = '127.0.0.1',
  [int]$Port = 4002,
  [switch]$NoLaunch,
  [switch]$SkipHealthcheck,
  [switch]$SkipRegression,
  [switch]$Strict
)

$script = Join-Path $ProjectRoot 'scripts\ci_gate_pr.ps1'
if(!(Test-Path $script)){
  Write-Error "Missing gate script: $script"
  exit 90
}

$invokeArgs = @{
  ProjectRoot = $ProjectRoot
  PythonExe = $PythonExe
  IbHost = $IbHost
  Port = $Port
}
if($NoLaunch){ $invokeArgs.NoLaunch = $true }
if($SkipHealthcheck){ $invokeArgs.SkipHealthcheck = $true }
if($SkipRegression){ $invokeArgs.SkipRegression = $true }
if($Strict){ $invokeArgs.Strict = $true }

& $script @invokeArgs
exit $LASTEXITCODE
