param(
  [ValidateSet('A','B','C','D')]
  [string]$Batch
)

$ErrorActionPreference='Stop'

$root = 'D:\quant\HeptaTrader-master'
Set-Location $root

git restore --staged . | Out-Null

$files = @()
switch($Batch){
  'A' {
    $files = @(
      'HeptaTrade/adapter_ib/ib_gateway_adapter.h',
      'HeptaTrade/adapter_ib/ib_gateway_adapter.cpp',
      'docs/IB-PROD-HARDENING.md'
    )
  }
  'B' {
    $files = @(
      'HeptaTrade/adapter_ctp/ctp_gateway_adapter.h',
      'HeptaTrade/adapter_ctp/ctp_gateway_adapter.cpp',
      'HeptaTrade/HeptaDemoStrategyTrader.cpp',
      'HeptaTrade/HeptaTrader.vcxproj',
      'HeptaTrade/HeptaTrader_Linux.vcxproj',
      'HeptaTrade/HeptaTraderConfig.xml.example',
      'HeptaTrade/HeptaTraderConfig.paper.xml',
      'HeptaTrade/adapter_xt/xt_gateway_adapter.h',
      'HeptaTrade/adapter_xt/xt_gateway_adapter.cpp'
    )
  }
  'C' {
    $files = @(
      'scripts/ci_gate.ps1',
      'scripts/ci_gate_release.ps1',
      'scripts/run_ib_regression_round.ps1',
      'scripts/check_reconcile_critical_block.ps1',
      'scripts/run_xt_scaffold_smoke.ps1',
      'docs/CI-GATE.md'
    )
  }
  'D' {
    $files = @(
      'docs/COMMIT-PLAN-IB-CTP.md',
      'docs/GATE-STABILITY-REPORT.md',
      'docs/PHASE-FREEZE-IB-CTP.md',
      'docs/QMT-BRIDGE-MVP.md',
      'docs/QMT-SDK-REVIEW.md',
      'docs/XT-HEPTA-MAPPING.md',
      'docs/XTQMT-VENUE-PLAN.md',
      'pic/simnow_screenshot.png',
      'pic/simnow_screenshot1.png'
    )
  }
}

$existing = @()
foreach($f in $files){ if(Test-Path $f){ $existing += $f } elseif(-not (Test-Path $f)){ if($f -like 'pic/*'){ $existing += $f } } }
if($existing.Count -eq 0){ throw "No files found for batch $Batch" }

git add -- $existing
Write-Output "BATCH=$Batch"
Write-Output 'STAGED_FILES:'
git diff --cached --name-only
