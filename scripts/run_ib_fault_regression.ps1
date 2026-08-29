param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master'
)

$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $ProjectRoot 'runtime-logs\ib-fault-regression'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$roundId = Get-Date -Format 'yyyyMMdd-HHmmss'
$roundDir = Join-Path $runtimeRoot $roundId
New-Item -ItemType Directory -Path $roundDir -Force | Out-Null

$scenarios = @(
  [ordered]@{ name = 'disconnect'; file = Join-Path $ProjectRoot 'scripts\fault_injection_disconnect.ps1' },
  [ordered]@{ name = 'duplicate_callbacks'; file = Join-Path $ProjectRoot 'scripts\fault_injection_duplicate_callbacks.ps1' },
  [ordered]@{ name = 'delayed_ack'; file = Join-Path $ProjectRoot 'scripts\fault_injection_delayed_ack.ps1' }
)

$results = @()
foreach($s in $scenarios){
  $stdout = Join-Path $roundDir ("{0}.stdout.log" -f $s.name)
  $stderr = Join-Path $roundDir ("{0}.stderr.log" -f $s.name)
  if(!(Test-Path $s.file)){
    $results += [ordered]@{ scenario = $s.name; pass = $false; exitCode = 90; detail = "missing script: $($s.file)"; stdout = $stdout; stderr = $stderr; reportJson = '' }
    continue
  }

  $outRoot = Join-Path $roundDir $s.name
  New-Item -ItemType Directory -Path $outRoot -Force | Out-Null

  $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$s.file,'-ProjectRoot',$ProjectRoot,'-OutputRoot',$outRoot)
  $p = Start-Process -FilePath 'powershell' -ArgumentList $argList -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  $p.WaitForExit()
  $code = $p.ExitCode

  $reportJson = ''
  if(Test-Path $stdout){
    $lines = Get-Content -LiteralPath $stdout -ErrorAction SilentlyContinue
    $match = $lines | Where-Object { $_ -like 'REPORT_JSON=*' } | Select-Object -Last 1
    if($match){ $reportJson = $match.Substring('REPORT_JSON='.Length) }
    if($null -eq $code){
      if(($lines | Where-Object { $_ -eq 'OVERALL=PASS' }).Count -gt 0){ $code = 0 }
      elseif(($lines | Where-Object { $_ -eq 'OVERALL=FAIL' }).Count -gt 0){ $code = 1 }
    }
  }
  if($null -eq $code){ $code = -1 }

  $results += [ordered]@{
    scenario = $s.name
    pass = ($code -eq 0)
    exitCode = $code
    detail = "exitCode=$code"
    stdout = $stdout
    stderr = $stderr
    reportJson = $reportJson
  }
}

$overall = 'PASS'
if(($results | Where-Object { -not $_.pass }).Count -gt 0){ $overall = 'FAIL' }

$summary = [ordered]@{
  roundId = $roundId
  overall = $overall
  roundDir = $roundDir
  scenarios = $results
}

$jsonPath = Join-Path $roundDir 'fault_regression_summary.json'
$txtPath = Join-Path $roundDir 'fault_regression_summary.txt'
$summary | ConvertTo-Json -Depth 8 | Out-File -FilePath $jsonPath -Encoding utf8

$lines = @(
  "ROUND_ID=$roundId",
  "OVERALL=$overall",
  "ROUND_DIR=$roundDir",
  "SUMMARY_JSON=$jsonPath"
)
foreach($r in $results){
  $tag = if($r.pass){ 'PASS' } else { 'FAIL' }
  $lines += ("SCENARIO_{0}={1} (exit={2})" -f $r.scenario.ToUpper(), $tag, $r.exitCode)
}
$lines -join "`r`n" | Out-File -FilePath $txtPath -Encoding utf8

Write-Host "ROUND_DIR=$roundDir"
Write-Host "SUMMARY_JSON=$jsonPath"
Write-Host "OVERALL=$overall"

if($overall -eq 'PASS'){ exit 0 }
exit 1
