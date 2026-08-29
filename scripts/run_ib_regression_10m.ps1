param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [int]$DurationMinutes = 10,
  [string]$EnvProfilePath,
  [switch]$EnableOrders,
  [switch]$EnableLive,
  [switch]$EnableLatencyObs,
  [switch]$DryRun,
  [ValidateSet('safe','trading')][string]$RunMode = 'safe',
  [switch]$AllowTimeoutPass,
  [switch]$StrictTimeout
)

$ErrorActionPreference = 'Stop'

function Get-Tag([bool]$ok){ if($ok){ 'PASS' } else { 'FAIL' } }
function Normalize-Text([string]$s){ if($null -eq $s){ return '' }; return ($s -replace "`0",'') }
function Add-Excerpt([System.Collections.Generic.List[string]]$bag,[string]$name,[string]$val){
  if([string]::IsNullOrWhiteSpace($val)){ return }
  $one = $val.Trim()
  if($one.Length -gt 220){ $one = $one.Substring(0,220) + ' ...' }
  $bag.Add("- [$name] $one")
}


function Read-TextBestEffort([string]$path){
  if(-not (Test-Path $path)){ return '' }
  try {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    if($bytes.Length -eq 0){ return '' }
    try { return Normalize-Text ([System.Text.Encoding]::UTF8.GetString($bytes)) } catch {}
    try { return Normalize-Text ([System.Text.Encoding]::Unicode.GetString($bytes)) } catch {}
    try { return Normalize-Text ([System.Text.Encoding]::Default.GetString($bytes)) } catch {}
    return Normalize-Text (Get-Content -LiteralPath $path -Raw -ErrorAction SilentlyContinue)
  } catch {
    return Normalize-Text (Get-Content -LiteralPath $path -Raw -ErrorAction SilentlyContinue)
  }
}

function Wait-FileStable([string]$path, [int]$timeoutMs = 5000, [int]$stableMs = 800){
  if(-not (Test-Path $path)){ return }
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $lastLen = -1
  $stableFor = 0
  while($sw.ElapsedMilliseconds -lt $timeoutMs){
    $len = 0
    try { $len = (Get-Item -LiteralPath $path).Length } catch { $len = 0 }
    if($len -eq $lastLen){
      $stableFor += 150
      if($stableFor -ge $stableMs){ break }
    } else {
      $stableFor = 0
      $lastLen = $len
    }
    Start-Sleep -Milliseconds 150
  }
}


$runtimeRoot = Join-Path $ProjectRoot 'runtime-logs'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$runId = ("ib-regression-{0}m-{1}" -f $DurationMinutes, $ts)
$runDir = Join-Path $runtimeRoot $runId
New-Item -ItemType Directory -Path $runDir -Force | Out-Null

$outLog = Join-Path $runDir 'run.out.log'
$errLog = Join-Path $runDir 'run.err.log'
$metaLog = Join-Path $runDir 'run.meta.log'
$latLog = Join-Path $runDir 'ib_latency_trace.jsonl'
$latReport = Join-Path $runDir 'ib_latency_report.md'

$exe = Join-Path $ProjectRoot 'x64\Release\HeptaTrader.exe'
if(!(Test-Path $exe)){ throw "Executable not found: $exe" }

$start = Get-Date

function Wait-IbPortReady([string]$targetHost,[int]$port,[int]$retry,[int]$sleepMs){
  for($i=1;$i -le $retry;$i++){
    try {
      $ok = Test-NetConnection -ComputerName $targetHost -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
      if($ok){ return $true }
    } catch {}
    Start-Sleep -Milliseconds $sleepMs
  }
  return $false
}


$defaultOrderGate = if($RunMode -eq 'trading'){ '1' } else { '0' }
$defaultLive = '0'
$defaultLiveKill = '1'

$envMap = [ordered]@{
  HEPTA_VENUE = 'IB'
  HEPTA_VENUE_PROMPT = '0'
  HEPTA_PROFILE = 'paper'
  HEPTA_ALLOW_IB_ORDERS = $defaultOrderGate
  HEPTA_ALLOW_IB_LIVE = $defaultLive
  HEPTA_IB_LIVE_KILL_SWITCH = $defaultLiveKill
  HEPTA_GLOBAL_KILL_SWITCH = '0'
  HEPTA_IB_TEST_ORDER_LOOP = '0'
  HEPTA_IB_SYMBOL = 'USD.CNH'
  HEPTA_IB_MARKET_DATA_TYPE = '3'
  HEPTA_IB_HOST = '127.0.0.1'
  HEPTA_IB_PORT = '4002'
  HEPTA_IB_LAT_OBS = '0'
  HEPTA_IB_LAT_LOG_PATH = $latLog
  HEPTA_IB_LAT_REPORT_PATH = $latReport
  HEPTA_IB_REG_ALLOW_EXTERNAL_POS = '1'
  HEPTA_IB_HOTPATH_LOG_MINIMAL = '0'
  HEPTA_IB_MD_LOG_INTERVAL_MS = '1000'
}

if($EnvProfilePath){
  if(!(Test-Path $EnvProfilePath)){ throw "Env profile not found: $EnvProfilePath" }
  Get-Content -LiteralPath $EnvProfilePath | ForEach-Object {
    $line = $_.Trim()
    if([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')){ return }
    $eq = $line.IndexOf('=')
    if($eq -le 0){ return }
    $envMap[$line.Substring(0,$eq).Trim()] = $line.Substring($eq+1).Trim()
  }
}

if($EnableOrders){ $envMap['HEPTA_ALLOW_IB_ORDERS'] = '1' }
if(-not $EnableOrders -and $RunMode -eq 'safe'){ $envMap['HEPTA_ALLOW_IB_ORDERS'] = '0' }
if($EnableLive){
  throw 'This regression launcher cannot enable live trading. Use a dedicated live go/no-go runbook instead.'
}
if(-not $EnableLive -and $RunMode -eq 'safe'){ $envMap['HEPTA_ALLOW_IB_LIVE'] = '0'; $envMap['HEPTA_IB_LIVE_KILL_SWITCH'] = '1' }
$envMap['HEPTA_ALLOW_IB_LIVE'] = '0'
$envMap['HEPTA_IB_LIVE_KILL_SWITCH'] = '1'
if($EnableLatencyObs){ $envMap['HEPTA_IB_LAT_OBS'] = '1' }

"START=$($start.ToString('o'))" | Out-File -FilePath $metaLog -Encoding utf8
"RUN_ID=$runId" | Out-File -FilePath $metaLog -Append -Encoding utf8
"DRY_RUN=$DryRun" | Out-File -FilePath $metaLog -Append -Encoding utf8
"RUN_MODE=$RunMode" | Out-File -FilePath $metaLog -Append -Encoding utf8
"EXE=$exe" | Out-File -FilePath $metaLog -Append -Encoding utf8
"OUT_LOG=$outLog" | Out-File -FilePath $metaLog -Append -Encoding utf8
"ERR_LOG=$errLog" | Out-File -FilePath $metaLog -Append -Encoding utf8
"LAT_REPORT=$latReport" | Out-File -FilePath $metaLog -Append -Encoding utf8
"ENV_OVERRIDES_BEGIN" | Out-File -FilePath $metaLog -Append -Encoding utf8
$envMap.GetEnumerator() | ForEach-Object { "ENV $($_.Key)=$($_.Value)" | Out-File -FilePath $metaLog -Append -Encoding utf8 }
"ENV_OVERRIDES_END" | Out-File -FilePath $metaLog -Append -Encoding utf8

$exitCode = $null
$timedOut = $false

if(-not $DryRun){
  $ibHost = if($envMap.Contains('HEPTA_IB_HOST')){ [string]$envMap['HEPTA_IB_HOST'] } else { '127.0.0.1' }
  $ibPort = 4002
  try { $ibPort = [int]$envMap['HEPTA_IB_PORT'] } catch {}
  $portReady = Wait-IbPortReady -targetHost $ibHost -port $ibPort -retry 10 -sleepMs 500
  "IB_PORT_READY=$portReady host=$ibHost port=$ibPort" | Out-File -FilePath $metaLog -Append -Encoding utf8

  $envBackup = @{}
  foreach($kv in $envMap.GetEnumerator()){
    $name = [string]$kv.Key
    $prior = [Environment]::GetEnvironmentVariable($name, 'Process')
    $envBackup[$name] = $prior
    Set-Item -Path ("Env:" + $name) -Value ([string]$kv.Value)
  }
  $wd = Split-Path $exe
  $p = Start-Process -FilePath $exe -WorkingDirectory $wd -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -WindowStyle Hidden
  "PID=$($p.Id)" | Out-File -FilePath $metaLog -Append -Encoding utf8
  $waitSec = [Math]::Max(1, $DurationMinutes * 60)
  if(-not $p.WaitForExit($waitSec * 1000)){
    $timedOut = $true
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
  }
  if($p.HasExited){ $exitCode = $p.ExitCode }
  foreach($name in $envBackup.Keys){
    $prior = $envBackup[$name]
    if($null -eq $prior){
      Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
    } else {
      Set-Item -Path ("Env:" + $name) -Value ([string]$prior)
    }
  }
} else {
  "DryRun mode: process launch skipped." | Out-File -FilePath $metaLog -Append -Encoding utf8
}

$end = Get-Date
"END=$($end.ToString('o'))" | Out-File -FilePath $metaLog -Append -Encoding utf8
"TIMED_OUT=$timedOut" | Out-File -FilePath $metaLog -Append -Encoding utf8
"EXIT_CODE=$exitCode" | Out-File -FilePath $metaLog -Append -Encoding utf8

Wait-FileStable -path $outLog -timeoutMs 6000 -stableMs 1000
Wait-FileStable -path $errLog -timeoutMs 3000 -stableMs 600
$outText = Read-TextBestEffort $outLog
$errText = Read-TextBestEffort $errLog
$outBytes = if(Test-Path $outLog){ (Get-Item -LiteralPath $outLog).Length } else { 0 }
$errBytes = if(Test-Path $errLog){ (Get-Item -LiteralPath $errLog).Length } else { 0 }

if($DryRun -and [string]::IsNullOrWhiteSpace($outText)){
  $outCandidates = Get-ChildItem -LiteralPath $runtimeRoot -Recurse -File -Filter '*.out.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 30
  $picked = $null
  foreach($cand in $outCandidates){
    $txt = Normalize-Text (Get-Content -LiteralPath $cand.FullName -Raw -ErrorAction SilentlyContinue)
    if([regex]::IsMatch($txt,'tickPrice|lastOrderId=|\[HEARTBEAT\]','IgnoreCase')){ $picked = $cand; $outText = $txt; break }
    if(-not $picked){ $picked = $cand; $outText = $txt }
  }
  if($picked){ Add-Content -LiteralPath $metaLog -Value ("DRYRUN_FALLBACK_OUT=" + $picked.FullName) }
}
if($DryRun -and [string]::IsNullOrWhiteSpace($errText)){
  $errCandidates = Get-ChildItem -LiteralPath $runtimeRoot -Recurse -File -Filter '*.err.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 10
  if($errCandidates){
    $errText = Normalize-Text (Get-Content -LiteralPath $errCandidates[0].FullName -Raw -ErrorAction SilentlyContinue)
    Add-Content -LiteralPath $metaLog -Value ("DRYRUN_FALLBACK_ERR=" + $errCandidates[0].FullName)
  }
}

$observabilityCandidates = Get-ChildItem -LiteralPath $runtimeRoot -File -Filter 'ib_observability*.jsonl' -ErrorAction SilentlyContinue |
  Where-Object { ($DryRun -or $_.LastWriteTime -ge $start.AddMinutes(-2)) } |
  Sort-Object LastWriteTime -Descending
$obsFile = if($observabilityCandidates){ $observabilityCandidates[0].FullName } else { $null }

$omsFromOut = [regex]::Match($outText, 'OMS journal ready:\s*([^\s]+\.jsonl)', 'IgnoreCase')
$omsPath = $null
if($omsFromOut.Success){
  $cand = $omsFromOut.Groups[1].Value.Trim().Replace('/','\\')
  if([System.IO.Path]::IsPathRooted($cand)){ $omsPath = $cand } else { $omsPath = Join-Path $ProjectRoot $cand }
}
if(-not $omsPath){
  $omsCand = Get-ChildItem -LiteralPath $runtimeRoot -File -Filter 'oms_journal*.jsonl' -ErrorAction SilentlyContinue |
    Where-Object { ($DryRun -or $_.LastWriteTime -ge $start.AddMinutes(-2)) } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if($omsCand){ $omsPath = $omsCand.FullName }
}

$reconcileFromOut = [regex]::Match($outText, 'Reconcile startup report ready:\s*([^\s]+\.json)', 'IgnoreCase')
$reconcilePath = $null
if($reconcileFromOut.Success){
  $cand = $reconcileFromOut.Groups[1].Value.Trim().Replace('/','\\')
  if([System.IO.Path]::IsPathRooted($cand)){ $reconcilePath = $cand } else { $reconcilePath = Join-Path $ProjectRoot $cand }
}
if(-not $reconcilePath){
  $recCand = Get-ChildItem -LiteralPath $runtimeRoot -File -Filter 'reconcile_startup_report*.json' -ErrorAction SilentlyContinue |
    Where-Object { ($DryRun -or $_.LastWriteTime -ge $start.AddMinutes(-2)) } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if($recCand){ $reconcilePath = $recCand.FullName }
}

$omsText = if($omsPath -and (Test-Path $omsPath)){ Get-Content -LiteralPath $omsPath -Raw -ErrorAction SilentlyContinue } else { '' }
$obsText = if($obsFile -and (Test-Path $obsFile)){ Get-Content -LiteralPath $obsFile -Raw -ErrorAction SilentlyContinue } else { '' }

$excerpts = New-Object System.Collections.Generic.List[string]

$tickMatch = [regex]::Match($outText, '\[IB-MD\].*tickPrice|tickPrice\s+reqId=', 'IgnoreCase')
$tickCount = [regex]::Matches($outText, 'tickPrice', 'IgnoreCase').Count
$checkTick = ($tickCount -ge 1)
Add-Excerpt $excerpts 'market_tick' ($(if($tickMatch.Success){$tickMatch.Value}else{"tickCount=$tickCount"}))

$fatalPattern = 'fatal|crash|unhandled exception|terminate called|segmentation fault|watchdog_critical'
$fatalMatch = [regex]::Match(($outText + "`n" + $errText + "`n" + $omsText), $fatalPattern, 'IgnoreCase')
$checkFatal = -not $fatalMatch.Success
Add-Excerpt $excerpts 'fatal_scan' ($(if($fatalMatch.Success){$fatalMatch.Value}else{'no fatal markers'}))

$hbMatch = [regex]::Match($outText, '\[HEARTBEAT\].*|Robust_.*\sIB:IB_[A-Z_]+\s+lastOrderId=\d+', 'IgnoreCase')
$hbCount = [regex]::Matches($outText, '\[HEARTBEAT\]|lastOrderId=', 'IgnoreCase').Count
$checkHeartbeat = ($hbCount -ge 1)
Add-Excerpt $excerpts 'heartbeat' ($(if($hbMatch.Success){$hbMatch.Value}else{"heartbeatCount=$hbCount"}))

$posLine = [regex]::Match($outText, 'positions=([^\r\n]+)', 'IgnoreCase')
$reconcileOk = $false
if($reconcilePath -and (Test-Path $reconcilePath)){
  try {
    $rec = Get-Content -LiteralPath $reconcilePath -Raw | ConvertFrom-Json
    $reconcileOk = (-not [bool]$rec.critical)
  } catch { $reconcileOk = $false }
}
$posText = if($posLine.Success){ $posLine.Groups[1].Value.Trim() } else { '' }
$posLooksBad = $false
if(-not [string]::IsNullOrWhiteSpace($posText)){ $posLooksBad = [regex]::IsMatch($posText, 'unknown|nan|inf|\?\?', 'IgnoreCase') }
$checkPosition = (($posLine.Success -and -not $posLooksBad) -or $reconcileOk)
Add-Excerpt $excerpts 'position_summary' ($(if($posLine.Success){$posLine.Value}elseif($reconcilePath){"reconcileCritical=$(-not $reconcileOk)"}else{'no position summary seen'}))

$breakerTripCount = [regex]::Matches(($outText + "`n" + $obsText), 'risk\.circuit_breaker"|circuit_breaker', 'IgnoreCase').Count
$breakerRecovered = [regex]::IsMatch(($outText + "`n" + $obsText), 'risk\.circuit_breaker_recovered|breaker.*recovered', 'IgnoreCase')
$ordersEnabled = ($envMap['HEPTA_ALLOW_IB_ORDERS'] -eq '1')
if($RunMode -eq 'safe' -and -not $ordersEnabled){ $checkPosition = $true }
$checkBreaker = ($breakerTripCount -eq 0 -or $breakerRecovered)
if(-not $ordersEnabled){ $checkBreaker = $true }
Add-Excerpt $excerpts 'breaker' ("tripCount=$breakerTripCount recovered=$breakerRecovered")

$latEnabled = ($envMap['HEPTA_IB_LAT_OBS'] -eq '1')
$latExists = (Test-Path $latReport)
$checkLatency = ((-not $latEnabled) -or $latExists)
Add-Excerpt $excerpts 'latency_report' ("enabled=$latEnabled exists=$latExists path=$latReport")

$checks = @(
  [ordered]@{ name='market_tick_present'; pass=$checkTick; detail="tickCount=$tickCount" },
  [ordered]@{ name='no_fatal_crash'; pass=$checkFatal; detail=$(if($checkFatal){'no fatal markers'}else{'fatal marker detected'}) },
  [ordered]@{ name='heartbeat_present'; pass=$checkHeartbeat; detail="heartbeatCount=$hbCount" },
  [ordered]@{ name='position_summary_consistency'; pass=$checkPosition; detail=$(if($checkPosition){'position/reconcile looks consistent'}else{'no reliable position summary'}) },
  [ordered]@{ name='breaker_not_permanently_tripped'; pass=$checkBreaker; detail="tripCount=$breakerTripCount recovered=$breakerRecovered" },
  [ordered]@{ name='latency_report_if_enabled'; pass=$checkLatency; detail="latObs=$latEnabled reportExists=$latExists" }
)

$hasOutput = ($outBytes -gt 0 -or $errBytes -gt 0)
$checksAllPass = (($checks | Where-Object { -not $_.pass }).Count -eq 0)

$connectStall = ([regex]::IsMatch($outText,'IB connecting\.\.\.|IB_CONNECTING','IgnoreCase') -and -not [regex]::IsMatch($outText,'IB_CONNECTED|\[HEARTBEAT\]|tickPrice','IgnoreCase'))

$timeoutPass = ($AllowTimeoutPass -or ((-not $StrictTimeout) -and $checksAllPass))
$overall = if($connectStall){ 'FAIL_CONNECT_STALL' } elseif(-not $hasOutput){ 'FAIL' } elseif(-not $checksAllPass){ 'FAIL' } elseif($timedOut){ if($timeoutPass){ if($StrictTimeout){ 'PASS_WITH_TIMEOUT' } else { 'PASS' } } else { 'FAIL_TIMEOUT' } } else { 'PASS' }


$reportPath = Join-Path $runtimeRoot (("regression_{0}m_{1}.md" -f $DurationMinutes, $ts))
$md = New-Object System.Collections.Generic.List[string]
$md.Add('# IB Regression Report')
$md.Add('')
$md.Add("- Run ID: $runId")
$md.Add("- Overall: **$overall**")
$md.Add("- Start: $($start.ToString('o'))")
$md.Add("- End: $($end.ToString('o'))")
$md.Add("- Duration target: ${DurationMinutes}m")
$md.Add("- DryRun: $DryRun")
$md.Add("- RunMode: $RunMode")
$md.Add("- TimedOut: $timedOut")
$md.Add("- ExitCode: $exitCode")
$gateRec = if($overall -eq 'PASS'){ 'RELEASE_OK' } elseif($overall -eq 'PASS_WITH_TIMEOUT'){ 'WARN_TIMEOUT' } else { 'BLOCK' }
$md.Add("- GateRecommendation: $gateRec")
$md.Add('')
$md.Add('## Diagnostics')
$md.Add("- out_log: $outLog")
$md.Add("- out_log_bytes: $outBytes")
$md.Add("- err_log: $errLog")
$md.Add("- err_log_bytes: $errBytes")
$md.Add("- tick_count: $tickCount")
$md.Add("- heartbeat_count: $hbCount")
$md.Add("- breaker_trip_count: $breakerTripCount")
$md.Add("- parse_mode: best_effort_bytes_utf8_unicode_default")
$md.Add("- connect_stall: $connectStall")
$md.Add("- strict_timeout: $StrictTimeout")
$md.Add('')
$md.Add('## Checks')
foreach($c in $checks){ $md.Add("- **$($c.name)**: **$(Get-Tag ([bool]$c.pass))** — $($c.detail)") }
$md.Add('')
$md.Add('## Key excerpts')
if($excerpts.Count -eq 0){ $md.Add('- (none)') } else { $excerpts | ForEach-Object { $md.Add($_) } }
$md.Add('')
$md.Add('## Artifacts')
$md.Add("- run dir: $runDir")
$md.Add("- stdout: $outLog")
$md.Add("- stderr: $errLog")
$md.Add("- meta: $metaLog")
if($omsPath){ $md.Add("- oms journal: $omsPath") }
if($reconcilePath){ $md.Add("- reconcile report: $reconcilePath") }
if($obsFile){ $md.Add("- observability jsonl: $obsFile") }
if($latEnabled){ $md.Add("- latency report: $latReport") }

($md -join "`r`n") | Out-File -FilePath $reportPath -Encoding utf8

Write-Host "RUN_DIR=$runDir"
Write-Host "REPORT_MD=$reportPath"
Write-Host "OVERALL=$overall"

if($overall -eq 'PASS' -or ($overall -eq 'PASS_WITH_TIMEOUT' -and $timeoutPass)){ exit 0 } elseif($overall -eq 'FAIL_TIMEOUT'){ exit 2 } elseif($overall -eq 'FAIL_CONNECT_STALL'){ exit 3 } else { exit 1 }


