param(
  [string]$IbHost = '127.0.0.1',
  [int]$Port = 4002,
  [string]$IbGatewayProcess = 'ibgateway',
  [string]$StrategyProcess = 'HeptaDemoStrategyTrader'
)

$ErrorActionPreference = 'Stop'

$hostNormalized = $IbHost.Trim().ToLowerInvariant()
$loopback = @('127.0.0.1','localhost','::1') -contains $hostNormalized
$ibCount = @(Get-Process -Name $IbGatewayProcess -ErrorAction SilentlyContinue).Count
$strategyCount = @(Get-Process -Name $StrategyProcess -ErrorAction SilentlyContinue).Count

$listen = $false
try {
  $listen = @((Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)).Count -gt 0
} catch {}

$res = [ordered]@{
  timestamp = (Get-Date).ToString('o')
  host = $env:COMPUTERNAME
  ibHost = $IbHost
  port = $Port
  isLoopbackHost = $loopback
  localPortListening = $listen
  ibGatewayProcessCount = $ibCount
  strategyProcessCount = $strategyCount
  pass = ($loopback -and $ibCount -ge 1 -and $strategyCount -ge 1)
  detail = if($loopback){ 'IB host is loopback; validate process/runtime colocation on same host.' } else { 'IB host is remote; not colocated by config.' }
}

$logDir = Join-Path $PSScriptRoot '..\runtime-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$outPath = Join-Path $logDir 'ib_colocation_check.json'
$res | ConvertTo-Json -Depth 6 | Set-Content -Path $outPath -Encoding UTF8
Write-Host "Report written: $outPath"
Write-Host ("COLOCATION={0} :: loopback={1} ibProc={2} strategyProc={3}" -f ($(if($res.pass){'PASS'}else{'FAIL'}), $loopback, $ibCount, $strategyCount))
if($res.pass){ exit 0 } else { exit 1 }

