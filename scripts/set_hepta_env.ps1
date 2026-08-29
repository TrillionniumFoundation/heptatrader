param(
  [ValidateSet('IB','CTP','XT','AUTO')] [string]$Venue = 'AUTO',
  [switch]$PersistUser,
  [switch]$PrintOnly
)

# HeptaTrader unified env bootstrap (IB/CTP/XT)
$vars = [ordered]@{
  # global venue switches
  HEPTA_VENUE = $Venue
  HEPTA_VENUE_PROMPT = '1'

  # common risk toggles
  HEPTA_GLOBAL_KILL_SWITCH = '0'
  HEPTA_FLATTEN_ONLY = '0'

  # IB defaults
  HEPTA_ALLOW_IB_ORDERS = '1'
  HEPTA_ALLOW_IB_LIVE = '0'
  HEPTA_IB_LIVE_KILL_SWITCH = '1'
  HEPTA_IB_MARKET_DATA_TYPE = '1'
  HEPTA_IB_MAX_ORDER_QTY = '50000'
  HEPTA_IB_MAX_DAILY_ORDERS = '50000'
  HEPTA_IB_TEST_ORDER_LOOP = '0'
  HEPTA_IB_SYMBOL = 'USD.CNH'
  HEPTA_IB_STRATEGY = 'fx_trend,fx_scalping'

  # CTP placeholders (fill your credentials when needed)
  HEPTA_CTP_BROKER_ID = ''
  HEPTA_CTP_USER_ID = ''
  HEPTA_CTP_PASSWORD = ''
  HEPTA_CTP_MD_FRONT = ''
  HEPTA_CTP_TD_FRONT = ''

  # XT placeholders
  HEPTA_XT_ACCOUNT = ''
  HEPTA_XT_PATH = 'D:\国金证券QMT交易端'
}

if($PrintOnly){
  $vars.GetEnumerator() | ForEach-Object { "`$env:$($_.Key) = `"$($_.Value)`"" }
  return
}

foreach($kv in $vars.GetEnumerator()){
  Set-Item -Path ("Env:" + $kv.Key) -Value ([string]$kv.Value)
}

if($PersistUser){
  foreach($kv in $vars.GetEnumerator()){
    [Environment]::SetEnvironmentVariable($kv.Key, [string]$kv.Value, 'User')
  }
  Write-Host 'Saved to USER environment variables.'
}

Write-Host 'HeptaTrader env loaded for current PowerShell session.'
Write-Host ('Venue=' + $Venue)
Write-Host 'Tip: add this to your PowerShell profile for auto-load:'
Write-Host '  . D:\quant\HeptaTrader-master\scripts\set_hepta_env.ps1 -Venue AUTO'
