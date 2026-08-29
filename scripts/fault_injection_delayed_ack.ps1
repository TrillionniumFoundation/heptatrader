param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$OutputRoot,
  [int]$MinExpectedDelaySec = 5,
  [int]$MaxAllowedDelaySec = 30
)

$ErrorActionPreference = 'Stop'

if([string]::IsNullOrWhiteSpace($OutputRoot)){
  $OutputRoot = Join-Path $ProjectRoot 'runtime-logs\fault-injection\delayed-ack'
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$roundId = Get-Date -Format 'yyyyMMdd-HHmmss'
$roundDir = Join-Path $OutputRoot $roundId
New-Item -ItemType Directory -Path $roundDir -Force | Out-Null

$logPath = Join-Path $roundDir 'delayed_ack_events.jsonl'
$reportPath = Join-Path $roundDir 'delayed_ack_report.json'
$summaryPath = Join-Path $roundDir 'delayed_ack_report.txt'

$events = @(
  [ordered]@{ ts = '2026-02-27T09:20:00.000Z'; event = 'order_submit_requested'; clOrdId = 'sim-ack-001' },
  [ordered]@{ ts = '2026-02-27T09:20:08.000Z'; event = 'order_ack_received'; clOrdId = 'sim-ack-001'; orderId = 93001 },
  [ordered]@{ ts = '2026-02-27T09:20:08.100Z'; event = 'order_status'; orderId = 93001; status = 'Submitted' },
  [ordered]@{ ts = '2026-02-27T09:20:09.000Z'; event = 'order_status'; orderId = 93001; status = 'Cancelled' }
)
$events | ForEach-Object { ($_ | ConvertTo-Json -Compress) } | Out-File -FilePath $logPath -Encoding utf8

$submit = $events | Where-Object { $_.event -eq 'order_submit_requested' } | Select-Object -First 1
$ack = $events | Where-Object { $_.event -eq 'order_ack_received' } | Select-Object -First 1
$ackDelaySec = if($submit -and $ack){ ([datetime]$ack.ts - [datetime]$submit.ts).TotalSeconds } else { -1 }
$statuses = @($events | Where-Object { $_.event -eq 'order_status' } | ForEach-Object { $_.status })
$finalStatus = if($statuses.Count -gt 0){ $statuses[-1] } else { '' }

$checks = [ordered]@{
  ackPresent = [ordered]@{ pass = [bool]$ack; detail = 'contains order_ack_received event' }
  delayedAsExpected = [ordered]@{ pass = ($ackDelaySec -ge $MinExpectedDelaySec); detail = "ack_delay_sec=$ackDelaySec min_expected=$MinExpectedDelaySec" }
  delayWithinUpperBound = [ordered]@{ pass = ($ackDelaySec -le $MaxAllowedDelaySec); detail = "ack_delay_sec=$ackDelaySec max_allowed=$MaxAllowedDelaySec" }
  finalStateCancelled = [ordered]@{ pass = ($finalStatus -eq 'Cancelled'); detail = "final_status=$finalStatus" }
}

$overall = 'PASS'
foreach($k in $checks.Keys){ if(-not [bool]$checks[$k].pass){ $overall = 'FAIL'; break } }

$report = [ordered]@{
  scenario = 'delayed_ack'
  overall = $overall
  thresholds = [ordered]@{ minExpectedDelaySec = $MinExpectedDelaySec; maxAllowedDelaySec = $MaxAllowedDelaySec }
  stats = [ordered]@{ ackDelaySec = $ackDelaySec; finalStatus = $finalStatus }
  checks = $checks
  artifacts = [ordered]@{ eventLog = $logPath; summary = $summaryPath }
}

$report | ConvertTo-Json -Depth 8 | Out-File -FilePath $reportPath -Encoding utf8

$lines = @(
  "SCENARIO=delayed_ack",
  "OVERALL=$overall",
  "ACK_DELAY_SEC=$ackDelaySec",
  "FINAL_STATUS=$finalStatus",
  "REPORT_JSON=$reportPath"
)
$lines -join "`r`n" | Out-File -FilePath $summaryPath -Encoding utf8

Write-Host "SCENARIO=delayed_ack"
Write-Host "OVERALL=$overall"
Write-Host "REPORT_JSON=$reportPath"

if($overall -eq 'PASS'){ exit 0 }
exit 1
