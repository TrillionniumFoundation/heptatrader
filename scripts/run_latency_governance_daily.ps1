param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$IbHost = '127.0.0.1',
  [int]$IbPort = 4002,
  [switch]$ApplySystemTuning
)

$ErrorActionPreference = 'Stop'
$runtime = Join-Path $ProjectRoot 'runtime-logs'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $runtime ("latency-governance-daily-$ts")
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$mode = if($ApplySystemTuning){ 'Apply' } else { 'DryRun' }

Write-Host "[LATENCY-DAILY] step=system_tuning mode=$mode"
powershell -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot 'scripts\optimize_ib_host_latency.ps1') -Mode $mode -IbHost $IbHost -IbPort $IbPort | Out-File -FilePath (Join-Path $outDir 'system_tuning.log') -Encoding utf8

Write-Host "[LATENCY-DAILY] step=colocation_check"
powershell -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot 'scripts\check_ib_colocation.ps1') -IbHost $IbHost -Port $IbPort | Out-File -FilePath (Join-Path $outDir 'colocation_check.log') -Encoding utf8

Write-Host "[LATENCY-DAILY] step=release_check_core"
powershell -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot 'scripts\release_check.ps1') -ProjectRoot $ProjectRoot -Profile paper -SkipHealthcheck -SkipRegression -NoLaunch | Out-File -FilePath (Join-Path $outDir 'release_check.log') -Encoding utf8

Write-Host "[LATENCY-DAILY] output=$outDir"
