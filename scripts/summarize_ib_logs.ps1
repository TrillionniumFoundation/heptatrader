param(
  [string]$ProjectRoot = "D:\quant\HeptaTrader-master",
  [string]$WorkDir,
  [string]$OutDir,
  [int]$MinNextValidIdCount = 1,
  [int]$MinTickPriceCount = 1,
  [string]$CriticalErrorCodes = "201"
)

$ErrorActionPreference = 'Stop'

if([string]::IsNullOrWhiteSpace($WorkDir)){
  $WorkDir = Join-Path $ProjectRoot "x64\Debug"
}
if([string]::IsNullOrWhiteSpace($OutDir)){
  $OutDir = Join-Path $ProjectRoot "runtime-logs"
}

$runLog = Join-Path $WorkDir "run-gateway.log"
$traceLog = Join-Path $WorkDir "ib_connect_trace.log"

if(!(Test-Path $runLog) -or !(Test-Path $traceLog)){
  $latest = Get-ChildItem -Path (Join-Path $ProjectRoot "runtime-logs") -Directory -Filter "ib-healthcheck-*" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if($latest){
    if(!(Test-Path $runLog)){
      $altRun = Join-Path $latest.FullName "run-gateway.log"
      $altRunPrev = Join-Path $latest.FullName "run-gateway.prev.log"
      if(Test-Path $altRun){ $runLog = $altRun }
      elseif(Test-Path $altRunPrev){ $runLog = $altRunPrev }
    }
    if(!(Test-Path $traceLog)){
      $altTrace = Join-Path $latest.FullName "ib_connect_trace.log"
      $altTracePrev = Join-Path $latest.FullName "ib_connect_trace.prev.log"
      if(Test-Path $altTrace){ $traceLog = $altTrace }
      elseif(Test-Path $altTracePrev){ $traceLog = $altTracePrev }
    }
  }
}
if(!(Test-Path $runLog)){ throw "Missing: $runLog" }
if(!(Test-Path $traceLog)){ throw "Missing: $traceLog" }

$run = Get-Content -LiteralPath $runLog
$trace = Get-Content -LiteralPath $traceLog

$allText = ($run -join "`n") + "`n" + ($trace -join "`n")
$nextValid = [regex]::Matches($allText, 'nextValidId\s*=\s*(\d+)', 'IgnoreCase')
$ticks = [regex]::Matches($run -join "`n", 'tickPrice\s+reqId=(\d+)\s+price=([0-9]+(?:\.[0-9]+)?)', 'IgnoreCase')
$errors = [regex]::Matches($allText, 'error\s+id=([-0-9]+)\s+code=([0-9]+)\s+msg=([^\r\n]+)', 'IgnoreCase')

$codes = @{}
foreach($e in $errors){
  $c = $e.Groups[2].Value
  if(-not $codes.ContainsKey($c)){ $codes[$c] = 0 }
  $codes[$c]++
}

$criticalSet = @{}
foreach($c in ($CriticalErrorCodes -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })){
  $criticalSet[$c] = $true
}

$alerts = New-Object System.Collections.Generic.List[object]
if($nextValid.Count -lt $MinNextValidIdCount){
  $alerts.Add([ordered]@{ severity='P1'; rule='NO_NEXT_VALID_ID'; message='No nextValidId detected'; value=$nextValid.Count; threshold=$MinNextValidIdCount })
}
if($ticks.Count -lt $MinTickPriceCount){
  $alerts.Add([ordered]@{ severity='P2'; rule='LOW_TICK_PRICE_COUNT'; message='tickPrice count below threshold'; value=$ticks.Count; threshold=$MinTickPriceCount })
}
foreach($k in $codes.Keys){
  if($criticalSet.ContainsKey($k) -and $codes[$k] -gt 0){
    $alerts.Add([ordered]@{ severity='P1'; rule='CRITICAL_IB_ERROR_CODE'; message="Critical IB error code detected: $k"; code=$k; value=$codes[$k] })
  }
}

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$dest = Join-Path $OutDir ("ib-log-summary-{0}" -f $ts)
New-Item -ItemType Directory -Path $dest -Force | Out-Null

Copy-Item -LiteralPath $runLog -Destination (Join-Path $dest "run-gateway.log") -Force
Copy-Item -LiteralPath $traceLog -Destination (Join-Path $dest "ib_connect_trace.log") -Force

$report = @()
$report += "# IB Log Summary"
$report += "- Time: $(Get-Date -Format o)"
$report += "- run-gateway.log lines: $($run.Count)"
$report += "- ib_connect_trace.log lines: $($trace.Count)"
$report += "- nextValidId count: $($nextValid.Count)"
if($nextValid.Count -gt 0){ $report += "- latest nextValidId: $($nextValid[$nextValid.Count-1].Groups[1].Value)" }
$report += "- tickPrice count: $($ticks.Count)"
if($ticks.Count -gt 0){
  $report += "- latest tickPrice: $($ticks[$ticks.Count-1].Groups[2].Value)"
}
$report += "- error count: $($errors.Count)"
$report += ""
$report += "## Error code histogram"
if($codes.Keys.Count -eq 0){
  $report += "- (none)"
} else {
  foreach($k in ($codes.Keys | Sort-Object)){
    $report += "- $k : $($codes[$k])"
  }
}
$report += ""
$report += "## Alerts"
if($alerts.Count -eq 0){
  $report += "- (none)"
} else {
  foreach($a in $alerts){
    $report += "- [$($a.severity)] $($a.rule) - $($a.message)"
  }
}

$summaryObj = [ordered]@{
  timestamp = (Get-Date).ToString('o')
  source = [ordered]@{ runLog=$runLog; traceLog=$traceLog }
  metrics = [ordered]@{
    runLogLines = $run.Count
    traceLogLines = $trace.Count
    nextValidIdCount = $nextValid.Count
    latestNextValidId = $(if($nextValid.Count -gt 0){ [int]$nextValid[$nextValid.Count-1].Groups[1].Value } else { $null })
    tickPriceCount = $ticks.Count
    latestTickPrice = $(if($ticks.Count -gt 0){ [double]$ticks[$ticks.Count-1].Groups[2].Value } else { $null })
    errorCount = $errors.Count
    errorCodeHistogram = $codes
  }
  thresholds = [ordered]@{
    minNextValidIdCount = $MinNextValidIdCount
    minTickPriceCount = $MinTickPriceCount
    criticalErrorCodes = @($criticalSet.Keys)
  }
  alerts = $alerts
}

$reportPath = Join-Path $dest "summary.md"
$jsonPath = Join-Path $dest "summary.json"
$alertsPath = Join-Path $dest "alerts.json"
$report -join "`r`n" | Out-File -FilePath $reportPath -Encoding utf8
$summaryObj | ConvertTo-Json -Depth 8 | Out-File -FilePath $jsonPath -Encoding utf8
$alerts | ConvertTo-Json -Depth 6 | Out-File -FilePath $alertsPath -Encoding utf8

Write-Host "Summary written: $reportPath"
Write-Host "Summary JSON: $jsonPath"
Write-Host "Alerts JSON: $alertsPath"
Write-Host "Archive dir: $dest"
