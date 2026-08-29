param(
  [string]$ProjectRoot = "D:\quant\HeptaTrader-master",
  [string]$JournalPath = "runtime-logs/oms_journal.sample.jsonl",
  [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
  $ProjectRoot = Split-Path -Parent $scriptDir
}

$pyCmd = Get-Command $PythonExe -ErrorAction SilentlyContinue
if(-not $pyCmd){
  $pyCmd = Get-Command py -ErrorAction SilentlyContinue
  if(-not $pyCmd){ throw "Python not found. Please install Python or pass -PythonExe." }
  $PythonExe = "py"
}

function Invoke-Py([string[]]$Args){
  $argList = @()
  if($PythonExe -eq 'py'){ $argList += '-3' }
  $argList += $Args
  & $PythonExe @argList
  if($LASTEXITCODE -ne 0){
    throw "Python step failed with exit code $LASTEXITCODE :: $($Args -join ' ')"
  }
}

Write-Host "[1/2] generate sample journal"
Invoke-Py @((Join-Path $ProjectRoot 'scripts\gen_oms_journal_sample.py'))

Write-Host "[2/2] verify replay + schema"
Invoke-Py @((Join-Path $ProjectRoot 'scripts\verify_oms_journal_replay.py'), '--journal', $JournalPath)

Write-Host "[OK] OMS recover smoke passed"
