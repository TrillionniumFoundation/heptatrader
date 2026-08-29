param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$ExePath = 'D:\quant\HeptaTrader-master\x64\Release\HeptaTrader.exe',
  [string]$WorkDir = 'D:\quant\HeptaTrader-master\x64\Release',
  [int]$RunSec = 8
)

$ErrorActionPreference = 'Stop'

if(!(Test-Path $ExePath)){ throw "Executable not found: $ExePath" }
if(!(Test-Path $WorkDir)){ throw "WorkDir not found: $WorkDir" }

$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$artifactDir = Join-Path $ProjectRoot "runtime-logs\xt-scaffold-smoke-$ts"
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null

$env:HEPTA_VENUE = 'XT'
$env:HEPTA_XT_PATH = 'D:\国金证券QMT交易端\userdata'
$env:HEPTA_XT_ACCOUNT = 'XT_SIM'
$env:HEPTA_XT_ACCOUNT_TYPE = 'STOCK'
$env:HEPTA_XT_SESSION_ID = '88888'
$env:HEPTA_XT_SYMBOL = '000001.SZ'
$env:HEPTA_ALLOW_XT_ORDERS = '0'
$env:HEPTA_GLOBAL_KILL_SWITCH = '0'
$env:HEPTA_FLATTEN_ONLY = '0'

$proc = Start-Process -FilePath $ExePath -WorkingDirectory $WorkDir -PassThru -WindowStyle Hidden
Start-Sleep -Seconds $RunSec
if(-not $proc.HasExited){ Stop-Process -Id $proc.Id -Force }

$journal = Join-Path $WorkDir 'runtime-logs\oms_journal.jsonl'
$tailOut = Join-Path $artifactDir 'oms_journal.tail.txt'
$summary = Join-Path $artifactDir 'summary.txt'

$xtHit = $false
if(Test-Path $journal){
  Get-Content -Path $journal -Tail 200 | Set-Content -Path $tailOut -Encoding UTF8
  $txt = Get-Content -Path $tailOut -Raw -ErrorAction SilentlyContinue
  if($txt -match '"venue":"XT"' -or $txt -match 'xt\.bootstrap' -or $txt -match 'XT') { $xtHit = $true }
}

$lines = @()
$lines += "OVERALL=$(if($xtHit){'PASS'}else{'FAIL'})"
$lines += "ARTIFACT_DIR=$artifactDir"
$lines += "JOURNAL=$journal"
$lines += "TAIL=$tailOut"
$lines | Set-Content -Path $summary -Encoding UTF8

Write-Output "OVERALL=$(if($xtHit){'PASS'}else{'FAIL'})"
Write-Output "ARTIFACT_DIR=$artifactDir"
Write-Output "SUMMARY=$summary"

if($xtHit){ exit 0 } else { exit 1 }
