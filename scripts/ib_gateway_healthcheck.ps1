param(
  [string]$ProjectRoot = "D:\quant\HeptaTrader-master",
  [int]$TimeoutSec = 45,
  [int]$PollMs = 500,
  [switch]$NoLaunch,
  [string]$ExePath,
  [string]$WorkDir
)

$ErrorActionPreference = 'Stop'

function StatusLine([string]$name, [bool]$ok, [string]$detail){
  $tag = if($ok){ 'PASS' } else { 'FAIL' }
  $color = if($ok){ 'Green' } else { 'Red' }
  Write-Host ("[{0}] {1} - {2}" -f $tag, $name, $detail) -ForegroundColor $color
}

if([string]::IsNullOrWhiteSpace($ExePath)){
  $ExePath = Join-Path $ProjectRoot "x64\Debug\HeptaTrader.exe"
}
if([string]::IsNullOrWhiteSpace($WorkDir)){
  $WorkDir = Split-Path -Parent $ExePath
}

$runLog = Join-Path $WorkDir "run-gateway.log"
$traceLog = Join-Path $WorkDir "ib_connect_trace.log"

if(!(Test-Path $WorkDir)){ throw "WorkDir not found: $WorkDir" }
if(-not $NoLaunch -and !(Test-Path $ExePath)){ throw "Executable not found: $ExePath" }

$artifactRoot = Join-Path $ProjectRoot "runtime-logs"
New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$artifactDir = Join-Path $artifactRoot ("ib-healthcheck-{0}" -f $ts)
New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null

# Rotate current logs so each launched run is isolated.
if(-not $NoLaunch){
  if(Test-Path $runLog){ Move-Item -LiteralPath $runLog -Destination (Join-Path $artifactDir "run-gateway.prev.log") -Force }
  if(Test-Path $traceLog){ Move-Item -LiteralPath $traceLog -Destination (Join-Path $artifactDir "ib_connect_trace.prev.log") -Force }
}

$proc = $null
$launchError = $null
if(-not $NoLaunch){
  try {
    $proc = Start-Process -FilePath $ExePath -WorkingDirectory $WorkDir -PassThru -WindowStyle Hidden
  } catch {
    $launchError = $_.Exception.Message
  }
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$connected = $false
$nextValidIdOk = $false
$usdCnhTickOk = $false
$connectDetail = "No IB_CONNECTED/connected marker yet"
$nextDetail = "No nextValidId marker yet"
$tickDetail = "No USD/CNH tick yet"

while((Get-Date) -lt $deadline){
  $runContent = if(Test-Path $runLog){ Get-Content -LiteralPath $runLog -Raw -ErrorAction SilentlyContinue } else { "" }
  $traceContent = if(Test-Path $traceLog){ Get-Content -LiteralPath $traceLog -Raw -ErrorAction SilentlyContinue } else { "" }
  $all = ($runContent + "`n" + $traceContent)

  if(-not $connected){
    if($all -match 'IB_CONNECTED|IB connect returned:\s*true|IB_SOCKET_CONNECTED'){
      $connected = $true
      $connectDetail = "Detected IB connection marker"
    }
  }

  if(-not $nextValidIdOk){
    $m = [regex]::Match($all, 'nextValidId\s*=\s*(\d+)', 'IgnoreCase')
    if($m.Success){
      $nextValidIdOk = $true
      $nextDetail = "nextValidId=" + $m.Groups[1].Value
    } elseif($all -match 'IB_NEXTVALIDID_OK'){
      $nextValidIdOk = $true
      $nextDetail = "Detected IB_NEXTVALIDID_OK"
    }
  }

  if(-not $usdCnhTickOk){
    $hasProbeSub = $all -match 'USD/CASH/IDEALPRO/CNH|USD\.CNH|USDCNH'
    $tick = [regex]::Match($all, 'tickPrice\s+reqId=\d+\s+price=([0-9]+(?:\.[0-9]+)?)', 'IgnoreCase')
    if($hasProbeSub -and $tick.Success){
      $usdCnhTickOk = $true
      $tickDetail = "tickPrice=" + $tick.Groups[1].Value
    }
  }

  if($connected -and $nextValidIdOk -and $usdCnhTickOk){ break }
  Start-Sleep -Milliseconds $PollMs
}

if($proc -and -not $proc.HasExited){
  try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch {}
}

if(Test-Path $runLog){ Copy-Item -LiteralPath $runLog -Destination (Join-Path $artifactDir "run-gateway.log") -Force }
if(Test-Path $traceLog){ Copy-Item -LiteralPath $traceLog -Destination (Join-Path $artifactDir "ib_connect_trace.log") -Force }

$overall = $connected -and $nextValidIdOk -and $usdCnhTickOk -and (-not $launchError)

StatusLine "CONNECTIVITY" $connected $connectDetail
StatusLine "NEXT_VALID_ID" $nextValidIdOk $nextDetail
StatusLine "USD_CNH_TICK" $usdCnhTickOk $tickDetail
if($launchError){ StatusLine "PROCESS_LAUNCH" $false $launchError }

$summary = [ordered]@{
  timestamp = (Get-Date).ToString('o')
  projectRoot = $ProjectRoot
  exePath = $ExePath
  workDir = $WorkDir
  timeoutSec = $TimeoutSec
  launched = (-not $NoLaunch)
  launchError = $launchError
  checks = [ordered]@{
    connectivity = [ordered]@{ pass = $connected; detail = $connectDetail }
    nextValidId = [ordered]@{ pass = $nextValidIdOk; detail = $nextDetail }
    usdCnhTick = [ordered]@{ pass = $usdCnhTickOk; detail = $tickDetail }
  }
  overall = if($overall){ 'PASS' } else { 'FAIL' }
  artifacts = [ordered]@{
    dir = $artifactDir
    runLog = (Join-Path $artifactDir "run-gateway.log")
    traceLog = (Join-Path $artifactDir "ib_connect_trace.log")
  }
}

$summaryJson = $summary | ConvertTo-Json -Depth 6
$summaryTxt = @(
  "OVERALL=" + $summary.overall,
  "CONNECTIVITY=" + ($(if($connected){'PASS'} else {'FAIL'})) + " :: " + $connectDetail,
  "NEXT_VALID_ID=" + ($(if($nextValidIdOk){'PASS'} else {'FAIL'})) + " :: " + $nextDetail,
  "USD_CNH_TICK=" + ($(if($usdCnhTickOk){'PASS'} else {'FAIL'})) + " :: " + $tickDetail,
  "ARTIFACT_DIR=" + $artifactDir
) -join "`r`n"

$summaryJson | Out-File -FilePath (Join-Path $artifactDir "summary.json") -Encoding utf8
$summaryTxt | Out-File -FilePath (Join-Path $artifactDir "summary.txt") -Encoding utf8

Write-Host "\nOVERALL: $($summary.overall)" -ForegroundColor $(if($overall){'Green'} else {'Red'})
Write-Host "Artifacts: $artifactDir"

if($overall){ exit 0 } else { exit 1 }
