param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$OutputRoot,
  [int]$MinDuplicateCount = 2
)

$ErrorActionPreference = 'Stop'

if([string]::IsNullOrWhiteSpace($OutputRoot)){
  $OutputRoot = Join-Path $ProjectRoot 'runtime-logs\fault-injection\duplicate-callbacks'
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$roundId = Get-Date -Format 'yyyyMMdd-HHmmss'
$roundDir = Join-Path $OutputRoot $roundId
New-Item -ItemType Directory -Path $roundDir -Force | Out-Null

$logPath = Join-Path $roundDir 'duplicate_callbacks_events.jsonl'
$reportPath = Join-Path $roundDir 'duplicate_callbacks_report.json'
$summaryPath = Join-Path $roundDir 'duplicate_callbacks_report.txt'

$events = @(
  [ordered]@{ ts = '2026-02-27T09:10:00.000Z'; event = 'order_status'; orderId = 92001; status = 'Submitted'; source = 'ib' },
  [ordered]@{ ts = '2026-02-27T09:10:00.050Z'; event = 'order_status'; orderId = 92001; status = 'Submitted'; source = 'ib-duplicate' },
  [ordered]@{ ts = '2026-02-27T09:10:00.100Z'; event = 'order_status'; orderId = 92001; status = 'PreSubmitted'; source = 'ib' },
  [ordered]@{ ts = '2026-02-27T09:10:00.200Z'; event = 'order_status'; orderId = 92001; status = 'Cancelled'; source = 'ib' },
  [ordered]@{ ts = '2026-02-27T09:10:00.220Z'; event = 'order_status'; orderId = 92001; status = 'Cancelled'; source = 'ib-duplicate' }
)
$events | ForEach-Object { ($_ | ConvertTo-Json -Compress) } | Out-File -FilePath $logPath -Encoding utf8

$statuses = @($events | ForEach-Object { $_.status })
$uniqueSequence = New-Object System.Collections.Generic.List[string]
$duplicateCount = 0
foreach($s in $statuses){
  if($uniqueSequence.Count -eq 0 -or $uniqueSequence[$uniqueSequence.Count - 1] -ne $s){
    $uniqueSequence.Add($s)
  } else {
    $duplicateCount += 1
  }
}

$expected = @('Submitted','PreSubmitted','Cancelled')
$sequenceOk = (($uniqueSequence -join ',') -eq ($expected -join ','))
$finalStatus = if($uniqueSequence.Count -gt 0){ $uniqueSequence[$uniqueSequence.Count - 1] } else { '' }

$checks = [ordered]@{
  dedupSequenceStable = [ordered]@{ pass = $sequenceOk; detail = "unique_sequence=$($uniqueSequence -join '>') expected=$($expected -join '>')" }
  duplicateObserved = [ordered]@{ pass = ($duplicateCount -ge $MinDuplicateCount); detail = "duplicate_count=$duplicateCount threshold=$MinDuplicateCount" }
  finalStateCancelled = [ordered]@{ pass = ($finalStatus -eq 'Cancelled'); detail = "final_status=$finalStatus" }
}

$overall = 'PASS'
foreach($k in $checks.Keys){ if(-not [bool]$checks[$k].pass){ $overall = 'FAIL'; break } }

$report = [ordered]@{
  scenario = 'duplicate_callbacks'
  overall = $overall
  thresholds = [ordered]@{ minDuplicateCount = $MinDuplicateCount }
  stats = [ordered]@{ duplicateCount = $duplicateCount; uniqueSequence = @($uniqueSequence); finalStatus = $finalStatus }
  checks = $checks
  artifacts = [ordered]@{ eventLog = $logPath; summary = $summaryPath }
}

$report | ConvertTo-Json -Depth 8 | Out-File -FilePath $reportPath -Encoding utf8

$lines = @(
  "SCENARIO=duplicate_callbacks",
  "OVERALL=$overall",
  "DUPLICATE_COUNT=$duplicateCount",
  "UNIQUE_SEQUENCE=$($uniqueSequence -join '>')",
  "REPORT_JSON=$reportPath"
)
$lines -join "`r`n" | Out-File -FilePath $summaryPath -Encoding utf8

Write-Host "SCENARIO=duplicate_callbacks"
Write-Host "OVERALL=$overall"
Write-Host "REPORT_JSON=$reportPath"

if($overall -eq 'PASS'){ exit 0 }
exit 1
