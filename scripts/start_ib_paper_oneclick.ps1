param(
  [string]$IbHost = '127.0.0.1',
  [int]$Port = 4002,
  [int]$ClientId = 920,
  [string]$Account = 'DU1234567',
  [switch]$TestOrderLoop,
  [int]$CancelDelaySec = 5,
  [switch]$AllowLive
)

$ErrorActionPreference = 'Stop'
$root = 'D:\quant\HeptaTrader-master'
$wd = Join-Path $root 'x64\Release'
$exe = Join-Path $wd 'HeptaTrader.exe'
if(!(Test-Path $exe)){ throw "Executable not found: $exe" }

$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $root ("runtime-logs\paper-oneclick-$ts")
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

# Reconcile inputs (avoid startup block)
$open = Join-Path $runDir 'broker_open_orders.csv'
$pos  = Join-Path $runDir 'broker_positions.csv'
$cash = Join-Path $runDir 'broker_cash.txt'
$oms  = Join-Path $runDir 'oms_journal.jsonl'
"" | Set-Content $open -Encoding UTF8
"symbol,qty`nUSD.CNH,0`n" | Set-Content $pos -Encoding UTF8
"0" | Set-Content $cash -Encoding UTF8
New-Item -ItemType File -Path $oms -Force | Out-Null

$configPath = Join-Path $wd 'HeptaTraderConfig.xml'
$config = @"
<?xml version="1.0" ?>
<Config>
  <User>
    <MarketDataServer Front="" BrokerID="" UserID="" PassWord=""/>
    <TradeServer Front="" BrokerID="" UserID="" PassWord="" ProductInfo="Hepta" AppID="" AuthCode="" DllPath=""/>
  </User>
  <Subscription></Subscription>
  <IBServer Mode="IB" Host="$IbHost" Port="$Port" ClientId="$ClientId" Account="$Account" ReadOnly="0" />
  <StrategyConfigFile></StrategyConfigFile>
</Config>
"@
$config | Out-File -FilePath $configPath -Encoding ascii

$outLog = Join-Path $runDir 'run.out.log'
$errLog = Join-Path $runDir 'run.err.log'
$trace  = Join-Path $runDir 'ib-trace.log'

Write-Host "RUN_DIR=$runDir"
Write-Host "OUT_LOG=$outLog"
Write-Host "ERR_LOG=$errLog"
Write-Host "TRACE=$trace"
Write-Host "Starting HeptaTrader... Press Ctrl+C to stop."

function Get-EnvOrDefault([string]$name,[string]$def){ $v=[Environment]::GetEnvironmentVariable($name,'User'); if([string]::IsNullOrWhiteSpace($v)){ return $def }; return $v }

Push-Location $wd
try {
  if($AllowLive){
    throw 'This paper launcher cannot enable live trading. Use a dedicated live go/no-go runbook instead.'
  }

  $env:HEPTA_CONFIG_PATH = $configPath
  $env:HEPTA_PROFILE = 'paper'
  $env:HEPTA_ALLOW_IB_ORDERS = '1'
  $env:HEPTA_IB_MAX_ORDER_QTY = (Get-EnvOrDefault 'HEPTA_IB_MAX_ORDER_QTY' '1000000')
  $env:HEPTA_IB_MAX_DAILY_ORDERS = (Get-EnvOrDefault 'HEPTA_IB_MAX_DAILY_ORDERS' '1000000')
  $env:HEPTA_IB_CANCEL_DELAY_SEC = "$CancelDelaySec"
  $env:HEPTA_IB_MARKET_DATA_TYPE = (Get-EnvOrDefault 'HEPTA_IB_MARKET_DATA_TYPE' '1')
  $env:HEPTA_IB_TRACE = '1'
  $env:HEPTA_IB_TRACE_FILE = $trace

  # Reconcile path binding
  $env:HEPTA_OMS_JOURNAL_PATH = $oms
  $env:HEPTA_BROKER_OPEN_ORDERS_PATH = $open
  $env:HEPTA_BROKER_POSITIONS_PATH = $pos
  $env:HEPTA_BROKER_CASH_PATH = $cash
  $env:HEPTA_RECONCILE_REPORT_PATH = (Join-Path $runDir 'reconcile_startup_report.json')

  # Paper launcher invariant: live remains OFF.
  $env:HEPTA_ALLOW_IB_LIVE = '0'
  $env:HEPTA_IB_LIVE_KILL_SWITCH = '1'

  if($TestOrderLoop){
    $env:HEPTA_IB_TEST_ORDER_LOOP = '1'
    Write-Host 'Mode: IB test order loop (place->cancel).'
  } else {
    Remove-Item Env:HEPTA_IB_TEST_ORDER_LOOP -ErrorAction SilentlyContinue
    Write-Host 'Mode: strategy loop (no forced test loop).'
  }

  & $exe 2> $errLog | Tee-Object -FilePath $outLog
}
finally {
  Pop-Location
}
