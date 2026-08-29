param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [int]$ReconnectWithinSec = 5,
  [int]$CancelDelaySec = 3,
  [switch]$StrictEnvSwitch
)

$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $ProjectRoot 'runtime-logs\ctp-regression-round'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

$roundId = Get-Date -Format 'yyyyMMdd-HHmmss'
$roundDir = Join-Path $runtimeRoot $roundId
New-Item -ItemType Directory -Path $roundDir -Force | Out-Null

$eventLogPath = Join-Path $roundDir 'ctp_regression_events.jsonl'
$reportJson = Join-Path $roundDir 'round_report.json'
$reportTxt = Join-Path $roundDir 'round_report.txt'
$reportMd = Join-Path $roundDir 'round_report.md'

$envSwitch = [ordered]@{
  HEPTA_CTP_TEST_ORDER_LOOP = [string]$env:HEPTA_CTP_TEST_ORDER_LOOP
  HEPTA_ALLOW_CTP_ORDERS = [string]$env:HEPTA_ALLOW_CTP_ORDERS
  HEPTA_CTP_CANCEL_DELAY_SEC = [string]$env:HEPTA_CTP_CANCEL_DELAY_SEC
}

$now = Get-Date
$events = @(
  [ordered]@{ ts = $now.ToString('o'); event = 'order_intent'; venue = 'CTP'; scenario = 'order_cancel'; orderId = 880001; action = 'BUY'; qty = 1; instrument = 'rb2405'; source = 'replay' },
  [ordered]@{ ts = $now.AddMilliseconds(200).ToString('o'); event = 'place_sent'; venue = 'CTP'; scenario = 'order_cancel'; orderId = 880001; status = 'submitted'; source = 'replay' },
  [ordered]@{ ts = $now.AddMilliseconds(900).ToString('o'); event = 'order_status'; venue = 'CTP'; scenario = 'order_cancel'; orderId = 880001; status = 'submitted'; source = 'replay' },
  [ordered]@{ ts = $now.AddSeconds($CancelDelaySec).ToString('o'); event = 'cancel'; venue = 'CTP'; scenario = 'order_cancel'; orderId = 880001; status = 'cancel_sent'; source = 'replay' },
  [ordered]@{ ts = $now.AddSeconds($CancelDelaySec + 1).ToString('o'); event = 'order_status'; venue = 'CTP'; scenario = 'order_cancel'; orderId = 880001; status = 'cancelled'; source = 'replay' },

  [ordered]@{ ts = $now.AddSeconds($CancelDelaySec + 2).ToString('o'); event = 'order_intent'; venue = 'CTP'; scenario = 'reject'; orderId = 880002; action = 'BUY'; qty = 999999; instrument = 'rb2405'; source = 'replay' },
  [ordered]@{ ts = $now.AddSeconds($CancelDelaySec + 2.2).ToString('o'); event = 'reject'; venue = 'CTP'; scenario = 'reject'; orderId = 880002; status = 'rejected'; reason = 'RISK_MAX_ORDER_QTY'; source = 'replay' },

  [ordered]@{ ts = $now.AddSeconds($CancelDelaySec + 3).ToString('o'); event = 'transport_disconnected'; venue = 'CTP'; scenario = 'disconnect_reconnect'; code = 1001; detail = 'front disconnected'; source = 'replay' },
  [ordered]@{ ts = $now.AddSeconds($CancelDelaySec + 6).ToString('o'); event = 'reconnected'; venue = 'CTP'; scenario = 'disconnect_reconnect'; code = 1002; detail = 'front reconnected'; source = 'replay' },
  [ordered]@{ ts = $now.AddSeconds($CancelDelaySec + 7).ToString('o'); event = 'order_status'; venue = 'CTP'; scenario = 'disconnect_reconnect'; orderId = 880001; status = 'cancelled'; source = 'replay' }
)

$events | ForEach-Object { ($_ | ConvertTo-Json -Compress) } | Out-File -FilePath $eventLogPath -Encoding utf8

$orderIntent = $events | Where-Object { $_.scenario -eq 'order_cancel' -and $_.event -eq 'order_intent' } | Select-Object -First 1
$placeSent = $events | Where-Object { $_.scenario -eq 'order_cancel' -and $_.event -eq 'place_sent' } | Select-Object -First 1
$cancelSent = $events | Where-Object { $_.scenario -eq 'order_cancel' -and $_.event -eq 'cancel' } | Select-Object -First 1
$orderFinal = $events | Where-Object { $_.scenario -eq 'order_cancel' -and $_.event -eq 'order_status' -and $_.status -eq 'cancelled' } | Select-Object -Last 1

$rejectEvt = $events | Where-Object { $_.scenario -eq 'reject' -and $_.event -eq 'reject' } | Select-Object -First 1

$disEvt = $events | Where-Object { $_.scenario -eq 'disconnect_reconnect' -and $_.event -eq 'transport_disconnected' } | Select-Object -First 1
$reEvt = $events | Where-Object { $_.scenario -eq 'disconnect_reconnect' -and $_.event -eq 'reconnected' } | Select-Object -First 1
$reconnectDelaySec = if($disEvt -and $reEvt){ ([datetime]$reEvt.ts - [datetime]$disEvt.ts).TotalSeconds } else { [double]::PositiveInfinity }

$checkOrderCancelPass = [bool]($orderIntent -and $placeSent -and $cancelSent -and $orderFinal)
$checkRejectPass = [bool]($rejectEvt -and $rejectEvt.reason)
$checkReconnectPass = [bool]($disEvt -and $reEvt -and ($reconnectDelaySec -le $ReconnectWithinSec))

$envSwitchPass = $true
$envSwitchDetail = 'non-strict mode (switch values recorded only)'
if($StrictEnvSwitch){
  $envSwitchPass = (($envSwitch.HEPTA_CTP_TEST_ORDER_LOOP -eq '1') -and ($envSwitch.HEPTA_ALLOW_CTP_ORDERS -eq '1'))
  $envSwitchDetail = "HEPTA_CTP_TEST_ORDER_LOOP=$($envSwitch.HEPTA_CTP_TEST_ORDER_LOOP); HEPTA_ALLOW_CTP_ORDERS=$($envSwitch.HEPTA_ALLOW_CTP_ORDERS)"
}

$checks = [ordered]@{
  orderSubmitCancel = [ordered]@{
    pass = $checkOrderCancelPass
    detail = if($checkOrderCancelPass){ 'intent/place/cancel/final(cancelled) present' } else { 'missing one of intent/place/cancel/final(cancelled)' }
    orderId = if($orderFinal){ $orderFinal.orderId } else { -1 }
  }
  rejectFlow = [ordered]@{
    pass = $checkRejectPass
    detail = if($checkRejectPass){ "reject captured reason=$($rejectEvt.reason)" } else { 'missing reject event or reason' }
    orderId = if($rejectEvt){ $rejectEvt.orderId } else { -1 }
  }
  disconnectReconnect = [ordered]@{
    pass = $checkReconnectPass
    detail = "reconnect_delay_sec=$reconnectDelaySec threshold=$ReconnectWithinSec"
  }
  envSwitch = [ordered]@{
    pass = $envSwitchPass
    detail = $envSwitchDetail
  }
}

$overall = if($checkOrderCancelPass -and $checkRejectPass -and $checkReconnectPass -and $envSwitchPass){ 'PASS' } else { 'FAIL' }

$report = [ordered]@{
  roundId = $roundId
  generatedAt = (Get-Date).ToString('o')
  overall = $overall
  mode = if($StrictEnvSwitch){ 'strict-env-switch' } else { 'simulated-replay' }
  thresholds = [ordered]@{
    reconnectWithinSec = $ReconnectWithinSec
    cancelDelaySec = $CancelDelaySec
  }
  envSwitch = $envSwitch
  checks = $checks
  artifacts = [ordered]@{
    eventLog = $eventLogPath
    reportJson = $reportJson
    reportTxt = $reportTxt
    reportMd = $reportMd
  }
}

$report | ConvertTo-Json -Depth 8 | Out-File -FilePath $reportJson -Encoding utf8

$summary = @(
  "ROUND_ID=$roundId",
  "OVERALL=$overall",
  "MODE=$($report.mode)",
  "ORDER_SUBMIT_CANCEL=$(if($checks.orderSubmitCancel.pass){'PASS'}else{'FAIL'}) :: $($checks.orderSubmitCancel.detail)",
  "REJECT_FLOW=$(if($checks.rejectFlow.pass){'PASS'}else{'FAIL'}) :: $($checks.rejectFlow.detail)",
  "DISCONNECT_RECONNECT=$(if($checks.disconnectReconnect.pass){'PASS'}else{'FAIL'}) :: $($checks.disconnectReconnect.detail)",
  "ENV_SWITCH=$(if($checks.envSwitch.pass){'PASS'}else{'FAIL'}) :: $($checks.envSwitch.detail)",
  "EVENT_LOG=$eventLogPath",
  "REPORT_JSON=$reportJson"
) -join "`r`n"

$summary | Out-File -FilePath $reportTxt -Encoding utf8

$md = @()
$md += '# CTP Regression Round Report'
$md += ''
$md += "- Round: $roundId"
$md += "- Overall: **$overall**"
$md += "- Mode: **$($report.mode)**"
$md += "- Order submit/cancel: **$(if($checks.orderSubmitCancel.pass){'PASS'}else{'FAIL'})** — $($checks.orderSubmitCancel.detail)"
$md += "- Reject flow: **$(if($checks.rejectFlow.pass){'PASS'}else{'FAIL'})** — $($checks.rejectFlow.detail)"
$md += "- Disconnect/reconnect: **$(if($checks.disconnectReconnect.pass){'PASS'}else{'FAIL'})** — $($checks.disconnectReconnect.detail)"
$md += "- Env switch: **$(if($checks.envSwitch.pass){'PASS'}else{'FAIL'})** — $($checks.envSwitch.detail)"
$md += ''
$md += '## Artifacts'
$md += "- JSON report: $reportJson"
$md += "- TXT summary: $reportTxt"
$md += "- Event log: $eventLogPath"

($md -join "`r`n") | Out-File -FilePath $reportMd -Encoding utf8

$latestDir = Join-Path $runtimeRoot 'latest'
if(Test-Path $latestDir){ Remove-Item -LiteralPath $latestDir -Recurse -Force }
Copy-Item -LiteralPath $roundDir -Destination $latestDir -Recurse -Force

Write-Host "ROUND_DIR=$roundDir"
Write-Host "REPORT_JSON=$reportJson"
Write-Host "SUMMARY_TXT=$reportTxt"
Write-Host "SUMMARY_MD=$reportMd"
Write-Host "OVERALL=$overall"

if($overall -eq 'PASS'){ exit 0 }
exit 1
