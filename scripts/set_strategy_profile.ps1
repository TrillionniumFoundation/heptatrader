param(
  [ValidateSet('safe','balanced','aggressive')] [string]$Profile = 'balanced'
)

$common = @{
  HEPTA_VENUE = 'IB'
  HEPTA_VENUE_PROMPT = '1'
  HEPTA_ALLOW_IB_ORDERS = '1'
  HEPTA_ALLOW_IB_LIVE = '0'
  HEPTA_IB_LIVE_KILL_SWITCH = '1'
  HEPTA_IB_MARKET_DATA_TYPE = '1'
  HEPTA_IB_SYMBOL = 'USD.CNH'
  HEPTA_IB_MAX_ORDER_QTY = '50000'
  HEPTA_IB_MAX_DAILY_ORDERS = '50000'
}

$profiles = @{
  safe = @{
    HEPTA_IB_STRATEGY = 'fx_scalping,fx_trend'
    HEPTA_IB_SCALP_QTY = '25000'
    HEPTA_IB_SCALP_SIGNAL_SEC = '3'
    HEPTA_IB_SCALP_SPREAD_BPS = '2.0'
    HEPTA_IB_SCALP_MIN_VOL_BPS = '0.8'
    HEPTA_IB_SCALP_TP_BPS = '12'
    HEPTA_IB_SCALP_SL_BPS = '15'
    HEPTA_IB_SCALP_HOLD_TIMEOUT_SEC = '30'
    HEPTA_IB_SCALP_COOLDOWN_SEC = '8'
    HEPTA_IB_FX_TREND_SIGNAL_BPS = '1.8'
    HEPTA_IB_FX_TREND_SIGNAL_INTERVAL = '8'
    HEPTA_IB_FX_TREND_COOLDOWN_SEC = '12'
  }
  balanced = @{
    HEPTA_IB_STRATEGY = 'fx_scalping,fx_trend,fx_momentum_burst'
    HEPTA_IB_SCALP_QTY = '25000'
    HEPTA_IB_SCALP_SIGNAL_SEC = '2'
    HEPTA_IB_SCALP_SPREAD_BPS = '2.5'
    HEPTA_IB_SCALP_MIN_VOL_BPS = '0.6'
    HEPTA_IB_SCALP_TP_BPS = '10'
    HEPTA_IB_SCALP_SL_BPS = '12'
    HEPTA_IB_SCALP_HOLD_TIMEOUT_SEC = '20'
    HEPTA_IB_SCALP_COOLDOWN_SEC = '5'
    HEPTA_IB_FX_TREND_SIGNAL_BPS = '1.5'
    HEPTA_IB_FX_TREND_SIGNAL_INTERVAL = '5'
    HEPTA_IB_FX_TREND_COOLDOWN_SEC = '10'
    HEPTA_IB_BURST_TRIGGER_BPS = '0.8'
    HEPTA_IB_BURST_SIGNAL_SEC = '1'
    HEPTA_IB_BURST_QTY = '25000'
  }
  aggressive = @{
    HEPTA_IB_STRATEGY = 'fx_scalping,fx_trend,fx_momentum_burst,fx_mean_revert'
    HEPTA_IB_SCALP_QTY = '25000'
    HEPTA_IB_SCALP_SIGNAL_SEC = '1'
    HEPTA_IB_SCALP_SPREAD_BPS = '999'
    HEPTA_IB_SCALP_MIN_VOL_BPS = '0'
    HEPTA_IB_SCALP_TP_BPS = '6'
    HEPTA_IB_SCALP_SL_BPS = '8'
    HEPTA_IB_SCALP_HOLD_TIMEOUT_SEC = '10'
    HEPTA_IB_SCALP_COOLDOWN_SEC = '0'
    HEPTA_IB_FX_TREND_SIGNAL_BPS = '0.8'
    HEPTA_IB_FX_TREND_SIGNAL_INTERVAL = '1'
    HEPTA_IB_FX_TREND_COOLDOWN_SEC = '1'
    HEPTA_IB_BURST_TRIGGER_BPS = '0.5'
    HEPTA_IB_BURST_SIGNAL_SEC = '1'
    HEPTA_IB_BURST_QTY = '25000'
    HEPTA_IB_MR_TRIGGER_BPS = '1.0'
    HEPTA_IB_MR_SIGNAL_SEC = '1'
    HEPTA_IB_MR_QTY = '25000'
  }
}

foreach($kv in $common.GetEnumerator()){
  [Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, 'User')
}
foreach($kv in $profiles[$Profile].GetEnumerator()){
  [Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, 'User')
}
Write-Host "Applied HEPTA strategy profile: $Profile (User env)"
