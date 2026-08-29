param(
  [int]$DurationMinutes = 30,
  [string]$EnvFile = 'scripts\ib_adv_scheduler_stress_30m.env',
  [switch]$AllowPaperOrders,
  [switch]$AllowLive,
  [string]$WorkingDir = 'D:\quant\HeptaTrader-master\x64\Release'
)

$ErrorActionPreference = 'Stop'
$root = 'D:\quant\HeptaTrader-master'
$exe = Join-Path $WorkingDir 'HeptaTrader.exe'
if(!(Test-Path $exe)){ throw "Executable not found: $exe" }

$envPath = if([System.IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $root $EnvFile }
if(!(Test-Path $envPath)){ throw "Env file not found: $envPath" }

Get-Content -LiteralPath $envPath | ForEach-Object {
  $line = $_.Trim()
  if($line -eq '' -or $line.StartsWith('#')){ return }
  $kv = $line.Split('=',2)
  if($kv.Length -eq 2){
    [Environment]::SetEnvironmentVariable($kv[0].Trim(), $kv[1].Trim(), 'Process')
  }
}

function Get-EnvOrDefault([string]$name,[string]$def){ $v=[Environment]::GetEnvironmentVariable($name,'User'); if([string]::IsNullOrWhiteSpace($v)){ return $def }; return $v }

if($AllowLive){
  throw 'This stress launcher cannot enable live trading. Use a dedicated live go/no-go runbook instead.'
}

if($AllowPaperOrders){ $env:HEPTA_ALLOW_IB_ORDERS='1' }
$env:HEPTA_IB_MARKET_DATA_TYPE=(Get-EnvOrDefault 'HEPTA_IB_MARKET_DATA_TYPE' '1')
$env:HEPTA_IB_MAX_ORDER_QTY=(Get-EnvOrDefault 'HEPTA_IB_MAX_ORDER_QTY' '1000000')
$env:HEPTA_IB_MAX_DAILY_ORDERS=(Get-EnvOrDefault 'HEPTA_IB_MAX_DAILY_ORDERS' '1000000')

# Stress launcher invariant: live remains OFF.
$env:HEPTA_ALLOW_IB_LIVE='0'
$env:HEPTA_IB_LIVE_KILL_SWITCH='1'

$logDir = Join-Path $root 'runtime-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$outLog = Join-Path $logDir ("ib-adv-stress-30m-$ts.out.log")
$errLog = Join-Path $logDir ("ib-adv-stress-30m-$ts.err.log")

$timeoutSec = [Math]::Max(60, $DurationMinutes * 60)
Write-Host "Starting stress run for $DurationMinutes min"
Write-Host "OUT=$outLog"
Write-Host "ERR=$errLog"

$p = Start-Process -FilePath $exe -WorkingDirectory $WorkingDir -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -WindowStyle Hidden
$timedOut = $false
try {
  Wait-Process -Id $p.Id -Timeout $timeoutSec -ErrorAction Stop
} catch {
  $timedOut = $true
}

if($timedOut -and (Get-Process -Id $p.Id -ErrorAction SilentlyContinue)){
  Stop-Process -Id $p.Id -Force
}

$exitCode = $null
try { $p.Refresh(); if($p.HasExited){ $exitCode = $p.ExitCode } } catch {}
Write-Host "EXIT_CODE=$exitCode TIMED_OUT=$timedOut"
