param(
  [string]$IbHost = '127.0.0.1',
  [int]$Port = 4002,
  [int]$ClientId = 901,
  [switch]$AllowOrders,
  [int]$CancelDelaySec = 5,
  [int]$TimeoutSec = 90
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$wd = Join-Path $root 'x64\Release'
$exe = Join-Path $wd 'HeptaTrader.exe'
if(!(Test-Path $exe)){ throw "Executable not found: $exe" }

$logDir = Join-Path $root 'runtime-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$outLog = Join-Path $logDir ("ib-paper-loop-$ts.out.log")
$errLog = Join-Path $logDir ("ib-paper-loop-$ts.err.log")
$meta = Join-Path $logDir ("ib-paper-loop-$ts.meta.log")

$configPath = Join-Path $wd 'HeptaTraderConfig.xml'
$config = @"
<?xml version="1.0" ?>
<Config>
  <User>
    <MarketDataServer Front="" BrokerID="" UserID="" PassWord=""/>
    <TradeServer Front="" BrokerID="" UserID="" PassWord="" ProductInfo="Hepta" AppID="" AuthCode="" DllPath=""/>
  </User>
  <Subscription></Subscription>
  <IBServer Mode="IB" Host="$IbHost" Port="$Port" ClientId="$ClientId" Account="" ReadOnly="0" />
  <StrategyConfigFile></StrategyConfigFile>
</Config>
"@
$config | Out-File -FilePath $configPath -Encoding ascii
$cfgSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $configPath).Hash.ToLowerInvariant()

"START=$(Get-Date -Format o)" | Out-File -FilePath $meta -Encoding utf8
"EXE=$exe" | Out-File -FilePath $meta -Append -Encoding utf8
"CONFIG=$configPath" | Out-File -FilePath $meta -Append -Encoding utf8
"PROFILE=paper" | Out-File -FilePath $meta -Append -Encoding utf8
"SHA256=$cfgSha" | Out-File -FilePath $meta -Append -Encoding utf8
"ALLOW_ORDERS=$($AllowOrders.IsPresent)" | Out-File -FilePath $meta -Append -Encoding utf8
Write-Host ("CONFIG_FINGERPRINT config_path={0} profile=paper sha256={1}" -f $configPath, $cfgSha)

function Get-EnvOrDefault([string]$name,[string]$def){ $v=[Environment]::GetEnvironmentVariable($name,'User'); if([string]::IsNullOrWhiteSpace($v)){ return $def }; return $v }

Push-Location $wd
try {
  $env:HEPTA_CONFIG_PATH = $configPath
  $env:HEPTA_PROFILE = 'paper'
  $env:HEPTA_CONFIG_SHA256 = $cfgSha
  $env:HEPTA_IB_TEST_ORDER_LOOP = '1'
  $env:HEPTA_IB_CANCEL_DELAY_SEC = "$CancelDelaySec"
  $env:HEPTA_IB_MARKET_DATA_TYPE = '1'
  $env:HEPTA_IB_MAX_ORDER_QTY = '1000000'
  $env:HEPTA_IB_MAX_DAILY_ORDERS = '1000000'
  if($AllowOrders){ $env:HEPTA_ALLOW_IB_ORDERS = '1' } else { Remove-Item Env:HEPTA_ALLOW_IB_ORDERS -ErrorAction SilentlyContinue }
  $env:HEPTA_ALLOW_IB_LIVE = '0'
  $env:HEPTA_IB_LIVE_KILL_SWITCH = '1'

  $p = Start-Process -FilePath $exe -WorkingDirectory $wd -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -WindowStyle Hidden
  "PID=$($p.Id)" | Out-File -FilePath $meta -Append -Encoding utf8

  $timedOut = $false
  try {
    Wait-Process -Id $p.Id -Timeout $TimeoutSec -ErrorAction Stop
  } catch {
    $timedOut = $true
  }

  if($timedOut){
    if(Get-Process -Id $p.Id -ErrorAction SilentlyContinue){
      Stop-Process -Id $p.Id -Force
    }
    "TIMEOUT=true" | Out-File -FilePath $meta -Append -Encoding utf8
  }

  $exit = $null
  try {
    $p.Refresh()
    if($p.HasExited){ $exit = $p.ExitCode }
  } catch {}
  "END=$(Get-Date -Format o)" | Out-File -FilePath $meta -Append -Encoding utf8
  "EXIT_CODE=$exit" | Out-File -FilePath $meta -Append -Encoding utf8

  Write-Host "OUT=$outLog"
  Write-Host "ERR=$errLog"
  Write-Host "META=$meta"
}
finally {
  Pop-Location
}
