param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [switch]$DryRun = $true
)

$ErrorActionPreference = 'Stop'
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $ProjectRoot "runtime-logs\xt-first-order-stage-$ts"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$summary = Join-Path $outDir 'summary.txt'
$lines = @()
$lines += 'STAGE=XT_FIRST_ORDER'
$lines += ('MODE=' + ($(if($DryRun){'DRY_RUN'}else{'LIVE'})))

# hard safety defaults
$env:HEPTA_VENUE='XT'
$env:HEPTA_XT_PATH='D:\国金证券QMT交易端\userdata'
$env:HEPTA_XT_ACCOUNT_TYPE='STOCK'
$env:HEPTA_XT_SESSION_ID='88888'
$env:HEPTA_XT_SYMBOL='000001.SZ'
$env:HEPTA_XT_MAX_ORDER_QTY='100'
$env:HEPTA_XT_MAX_DAILY_ORDERS='1'
$env:HEPTA_XT_MAX_PRICE_DEV_BPS='20'

if($DryRun){
  $env:HEPTA_ALLOW_XT_ORDERS='0'
  $lines += 'HEPTA_ALLOW_XT_ORDERS=0'
} else {
  $env:HEPTA_ALLOW_XT_ORDERS='1'
  $lines += 'HEPTA_ALLOW_XT_ORDERS=1'
}

$p = Join-Path $ProjectRoot 'scripts\xt_pretrade_final_check.ps1'
$preOut = Join-Path $outDir 'pretrade.stdout.log'
$preErr = Join-Path $outDir 'pretrade.stderr.log'
$proc = Start-Process -FilePath powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$p,'-ProjectRoot',$ProjectRoot) -PassThru -RedirectStandardOutput $preOut -RedirectStandardError $preErr
$proc.WaitForExit()
$lines += ('PRETRADE_EXIT=' + $proc.ExitCode)

if($DryRun){
  $smoke = Join-Path $ProjectRoot 'scripts\run_xt_scaffold_smoke.ps1'
  $smOut = Join-Path $outDir 'smoke.stdout.log'
  $smErr = Join-Path $outDir 'smoke.stderr.log'
  $sp = Start-Process -FilePath powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$smoke,'-ProjectRoot',$ProjectRoot,'-ExePath',"$ProjectRoot\x64\Release\HeptaTrader.exe",'-WorkDir',"$ProjectRoot\x64\Release",'-RunSec','8') -PassThru -RedirectStandardOutput $smOut -RedirectStandardError $smErr
  $sp.WaitForExit()
  $lines += ('SMOKE_EXIT=' + $sp.ExitCode)
  $ok = ($proc.ExitCode -eq 0 -and $sp.ExitCode -eq 0)
  $lines += ('OVERALL=' + ($(if($ok){'PASS'}else{'FAIL'})))
} else {
  $lines += 'OVERALL=HOLD_FOR_MANUAL_LIVE_CONFIRM'
}

$lines += ('ARTIFACT_DIR=' + $outDir)
$lines | Set-Content -Path $summary -Encoding UTF8
Write-Output ('SUMMARY=' + $summary)
if($DryRun){ if($ok){exit 0}else{exit 1} } else { exit 0 }
