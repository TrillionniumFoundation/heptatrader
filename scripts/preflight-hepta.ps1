param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [switch]$StrictSecretScan
)

$ErrorActionPreference = 'Stop'

function Fail($msg){
  Write-Host "[FAIL] $msg" -ForegroundColor Red
  exit 1
}
function Pass($msg){
  Write-Host "[ OK ] $msg" -ForegroundColor Green
}
function Warn($msg){
  Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

Write-Host "== Hepta Preflight ==" -ForegroundColor Cyan
Write-Host "ProjectRoot: $ProjectRoot"

$simCfg = Join-Path $ProjectRoot "HeptaSimulator\HeptaSimulatorConfig.xml"
$simExeDirCfg = Join-Path $ProjectRoot "HeptaSimulator\x64\Debug\HeptaSimulatorConfig.xml"
$tradeCfg = Join-Path $ProjectRoot "HeptaTrade\HeptaTraderConfig.xml"
$instrumentXml = Join-Path $ProjectRoot "HeptaTrade\Instrument.xml"

# 1) Required files
foreach($f in @($simCfg,$instrumentXml)){
  if(!(Test-Path $f)){ Fail "missing required file: $f" }
  Pass "exists: $f"
}

# 2) Simulator config in exe dir (runtime expectation)
if(Test-Path $simExeDirCfg){
  Pass "runtime config present: $simExeDirCfg"
}else{
  Warn "runtime config missing: $simExeDirCfg (simulator may fail to load config)"
}

# 3) XML parse + path checks
[xml]$simCfgXml = Get-Content -LiteralPath $simCfg -Raw
$frontPath = $simCfgXml.Config.User.SimulatorServer.Front
$insPath = $simCfgXml.Config.User.SimulatorServer.Instrument
if([string]::IsNullOrWhiteSpace($frontPath)){ Fail "SimulatorServer.Front is empty in HeptaSimulatorConfig.xml" }
if([string]::IsNullOrWhiteSpace($insPath)){ Fail "SimulatorServer.Instrument is empty in HeptaSimulatorConfig.xml" }

if(Test-Path $frontPath){ Pass "SimulatorServer.Front exists: $frontPath" } else { Warn "SimulatorServer.Front not found: $frontPath" }
if(Test-Path $insPath){ Pass "SimulatorServer.Instrument exists: $insPath" } else { Warn "SimulatorServer.Instrument not found: $insPath" }

# 4) HisMarketDataIndex sanity + MD files existence
$simIdx = $frontPath
if(!(Test-Path $simIdx)){ Fail "HisMarketDataIndex file not found (from SimulatorServer.Front): $simIdx" }
Pass "index file exists (from SimulatorServer.Front): $simIdx"

[xml]$idxXml = Get-Content -LiteralPath $simIdx -Raw
$mdFiles = @($idxXml.HisMDFiles.MDFile)
if($mdFiles.Count -eq 0){ Fail "HisMarketDataIndex.xml has zero MDFile entries" }
Pass "HisMarketDataIndex.xml entries: $($mdFiles.Count)"

$missing = 0
foreach($n in $mdFiles){
  $p = [string]$n.FilePath
  if([string]::IsNullOrWhiteSpace($p)){ $missing++; Warn "MDFile has empty FilePath"; continue }
  if(!(Test-Path $p)){ $missing++; Warn "MD file missing: $p" }
}
if($missing -gt 0){
  Fail "missing/invalid MD files: $missing"
}else{
  Pass "all MD file paths exist"
}

# 5) Trade config secret scan (fail in strict mode)
if(Test-Path $tradeCfg){
  $raw = Get-Content -LiteralPath $tradeCfg -Raw
  $hasPwd = $raw -match 'PassWord\s*=\s*"[^"]+"'
  $hasAuth = $raw -match 'AuthCode\s*=\s*"[^"]+"'
  $hasUser = $raw -match 'UserID\s*=\s*"[^"]+"'
  if($hasPwd -or $hasAuth -or $hasUser){
    $msg = "plaintext credentials detected in $tradeCfg"
    if($StrictSecretScan){ Fail $msg } else { Warn $msg }
  }else{
    Pass "no plaintext credential pattern in $tradeCfg"
  }
}else{
  Pass "trade runtime config absent in repo path (good for secret hygiene): $tradeCfg"
}

# 6) subscription vs instrument sanity (best effort)
try {
  [xml]$tradeXml = Get-Content -LiteralPath $tradeCfg -Raw
  $sub = @($tradeXml.Config.Subscription.Instrument | ForEach-Object { $_.ID } | Where-Object { $_ })
  if($sub.Count -gt 0){
    [xml]$insXml = Get-Content -LiteralPath $instrumentXml -Raw
    $insText = $insXml.OuterXml
    foreach($id in $sub){
      if($insText -match [regex]::Escape($id)){ Pass "subscription instrument appears in Instrument.xml: $id" }
      else { Warn "subscription instrument not found in Instrument.xml (best-effort check): $id" }
    }
  }else{
    Warn "no subscription instrument configured"
  }
} catch {
  Warn "subscription/instrument check skipped: $($_.Exception.Message)"
}

Pass "preflight completed"
exit 0
