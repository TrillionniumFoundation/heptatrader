param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$ReportPath
)

$ErrorActionPreference = 'Stop'

if([string]::IsNullOrWhiteSpace($ReportPath)){
  if(-not [string]::IsNullOrWhiteSpace($env:HEPTA_RECONCILE_REPORT_PATH)){
    $ReportPath = $env:HEPTA_RECONCILE_REPORT_PATH
  } else {
    $ReportPath = Join-Path $ProjectRoot 'runtime-logs\reconcile_startup_report.json'
  }
}

if(!(Test-Path $ReportPath)){
  Write-Host "OVERALL=FAIL"
  Write-Host "DETAIL=reconcile report missing"
  Write-Host "REPORT_JSON=$ReportPath"
  exit 1
}

$report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
$hasCritical = $false

if($null -ne $report.startup_action -and $null -ne $report.startup_action.has_critical){
  $hasCritical = [bool]$report.startup_action.has_critical
} elseif($null -ne $report.checks){
  $criticalCount = @($report.checks | Where-Object { "$($_.severity)" -eq 'CRITICAL' }).Count
  $hasCritical = ($criticalCount -gt 0)
} elseif("$($report.status)" -eq 'CRITICAL'){
  $hasCritical = $true
}

if($hasCritical){
  Write-Host "OVERALL=FAIL"
  Write-Host "DETAIL=reconcile report has CRITICAL findings"
  Write-Host "REPORT_JSON=$ReportPath"
  exit 1
}

Write-Host "OVERALL=PASS"
Write-Host "DETAIL=no critical reconcile findings"
Write-Host "REPORT_JSON=$ReportPath"
exit 0
