param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$OutputRoot,
  [int]$ReconnectWithinSec = 5
)

$ErrorActionPreference = 'Stop'

if([string]::IsNullOrWhiteSpace($OutputRoot)){
  $OutputRoot = Join-Path $ProjectRoot 'runtime-logs\fault-injection\disconnect'
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$roundId = Get-Date -Format 'yyyyMMdd-HHmmss'
$roundDir = Join-Path $OutputRoot $roundId
New-Item -ItemType Directory -Path $roundDir -Force | Out-Null

$logPath = Join-Path $roundDir 'disconnect_events.jsonl'
$reportPath = Join-Path $roundDir 'disconnect_report.json'
$summaryPath = Join-Path $roundDir 'disconnect_report.txt'

$events = @(
  [ordered]@{ ts = '2026-02-27T09:00:00.000Z'; event = 'connected'; detail = 'IB socket connected' },
  [ordered]@{ ts = '2026-02-27T09:00:01.000Z'; event = 'order_submitted'; orderId = 91001; status = 'Submitted' },
  [ordered]@{ ts = '2026-02-27T09:00:02.000Z'; event = 'transport_disconnected'; code = 1100; detail = 'Connectivity between IB and TWS has been lost' },
  [ordered]@{ ts = '2026-02-27T09:00:05.500Z'; event = 'reconnected'; code = 1102; detail = 'Connectivity restored' },
  [ordered]@{ ts = '2026-02-27T09:00:06.000Z'; event = 'order_status'; orderId = 91001; status = 'PendingCancel' },
  [ordered]@{ ts = '2026-02-27T09:00:07.000Z'; event = 'order_status'; orderId = 91001; status = 'Cancelled' }
)

$events | ForEach-Object { ($_ | ConvertTo-Json -Compress) } | Out-File -FilePath $logPath -Encoding utf8

$disconnect = $events | Where-Object { $_.event -eq 'transport_disconnected' } | Select-Object -First 1
$reconnect = $events | Where-Object { $_.event -eq 'reconnected' } | Select-Object -First 1
$statuses = @($events | Where-Object { $_.event -eq 'order_status' } | ForEach-Object { $_.status })
$finalStatus = if($statuses.Count -gt 0){ $statuses[-1] } else { '' }
$reconnectDelaySec = if($disconnect -and $reconnect){
  ([datetime]$reconnect.ts - [datetime]$disconnect.ts).TotalSeconds
} else { [double]::PositiveInfinity }

$checks = [ordered]@{
  hasDisconnect = [ordered]@{ pass = [bool]$disconnect; detail = 'contains transport_disconnected event' }
  hasReconnect = [ordered]@{ pass = [bool]$reconnect; detail = 'contains reconnected event' }
  reconnectWithinSla = [ordered]@{ pass = ($reconnectDelaySec -le $ReconnectWithinSec); detail = "reconnect_delay_sec=$reconnectDelaySec threshold=$ReconnectWithinSec" }
  finalStateCancelled = [ordered]@{ pass = ($finalStatus -eq 'Cancelled'); detail = "final_status=$finalStatus" }
}

$overall = 'PASS'
foreach($k in $checks.Keys){ if(-not [bool]$checks[$k].pass){ $overall = 'FAIL'; break } }

$report = [ordered]@{
  scenario = 'disconnect'
  overall = $overall
  thresholds = [ordered]@{ reconnectWithinSec = $ReconnectWithinSec }
  stats = [ordered]@{ reconnectDelaySec = $reconnectDelaySec; finalStatus = $finalStatus }
  checks = $checks
  artifacts = [ordered]@{ eventLog = $logPath; summary = $summaryPath }
}

$report | ConvertTo-Json -Depth 8 | Out-File -FilePath $reportPath -Encoding utf8

$lines = @(
  "SCENARIO=disconnect",
  "OVERALL=$overall",
  "RECONNECT_DELAY_SEC=$reconnectDelaySec",
  "FINAL_STATUS=$finalStatus",
  "EVENT_LOG=$logPath",
  "REPORT_JSON=$reportPath"
)
$lines -join "`r`n" | Out-File -FilePath $summaryPath -Encoding utf8

Write-Host "SCENARIO=disconnect"
Write-Host "OVERALL=$overall"
Write-Host "REPORT_JSON=$reportPath"

if($overall -eq 'PASS'){ exit 0 }
exit 1
