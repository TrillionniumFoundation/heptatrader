param(
  [string]$OutPath = 'D:\quant\HeptaTrader-master\runtime-logs\ib_user_env_snapshot.env'
)

$keys = @(
  'HEPTA_IB_MARKET_DATA_TYPE','HEPTA_ALLOW_IB_ORDERS','HEPTA_ALLOW_IB_LIVE','HEPTA_IB_LIVE_KILL_SWITCH',
  'HEPTA_IB_MAX_ORDER_QTY','HEPTA_IB_MAX_DAILY_ORDERS','HEPTA_IB_STRATEGY',
  'HEPTA_IB_FX_TREND_MAX_POSITION','HEPTA_IB_SCALP_QTY','HEPTA_IB_FX_SCALPING_MAX_POSITION','HEPTA_IB_BURST_QTY','HEPTA_IB_MR_QTY',
  'HEPTA_IB_STARTUP_RETRY_MS','HEPTA_IB_STARTUP_RETRY_MAX'
)

$lines = New-Object System.Collections.Generic.List[string]
foreach($k in $keys){
  $v = [Environment]::GetEnvironmentVariable($k,'User')
  if(-not [string]::IsNullOrWhiteSpace($v)){
    $lines.Add("$k=$v")
  }
}

$dir = Split-Path -Parent $OutPath
if($dir -and !(Test-Path $dir)){ New-Item -ItemType Directory -Path $dir | Out-Null }
$lines | Set-Content -LiteralPath $OutPath -Encoding utf8
Write-Host "EXPORTED=$OutPath"
