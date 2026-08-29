param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$ConfigPath,
  [ValidateSet('sim','paper','live')]
  [string]$Profile,
  [string]$PythonExe = 'python',
  [string]$IbHost = '127.0.0.1',
  [int]$Port = 4002,
  [int]$HealthTimeoutSec = 45,
  [int]$RegressionTimeoutSec = 90,
  [switch]$NoLaunch,
  [switch]$Strict
)

$ErrorActionPreference = 'Stop'

function Run-PsScript {
  param(
    [string]$Name,
    [string]$File,
    [string[]]$Args,
    [string]$Stdout,
    [string]$Stderr,
    [int]$TimeoutSec = 0
  )

  if(!(Test-Path $File)){
    return [pscustomobject]@{ name=$Name; pass=$false; exitCode=90; detail="missing:$File"; artifacts=@($Stdout,$Stderr) }
  }

  $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$File) + $Args
  $p = Start-Process -FilePath 'powershell' -ArgumentList $argList -PassThru -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr
  if($TimeoutSec -gt 0){
    if(-not $p.WaitForExit($TimeoutSec*1000)){
      Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
      return [pscustomobject]@{ name=$Name; pass=$false; exitCode=99; detail="timeout:${TimeoutSec}s"; artifacts=@($Stdout,$Stderr) }
    }
  } else {
    $p.WaitForExit()
  }

  $exit = [int]$p.ExitCode
  return [pscustomobject]@{ name=$Name; pass=([bool]($exit -eq 0)); exitCode=$exit; detail=''; artifacts=@($Stdout,$Stderr) }
}

$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$gateDir = Join-Path $ProjectRoot "runtime-logs\ci-gate-$ts"
New-Item -ItemType Directory -Force -Path $gateDir | Out-Null

$summaryJson = Join-Path $gateDir 'ci_gate_summary.json'
$summaryTxt = Join-Path $gateDir 'ci_gate_summary.txt'

$checks = @()

# BUILD
$buildOut = Join-Path $gateDir 'build.stdout.log'
$buildErr = Join-Path $gateDir 'build.stderr.log'
$msbuild = 'D:\VSstudio\MSBuild\Current\Bin\amd64\MSBuild.exe'
if(!(Test-Path $msbuild)){
  $checks += [pscustomobject]@{ name='BUILD'; pass=$false; exitCode=90; detail='msbuild_missing'; artifacts=@($buildOut,$buildErr) }
} else {
  $b = Start-Process -FilePath $msbuild -ArgumentList @('HeptaTrader.sln','/t:Build','/p:Configuration=Release','/p:Platform=x64','/m') -WorkingDirectory $ProjectRoot -PassThru -RedirectStandardOutput $buildOut -RedirectStandardError $buildErr
  $b.WaitForExit()
  $bExit = [int]$b.ExitCode
   $checks += [pscustomobject]@{ name='BUILD'; pass=([bool]($bExit -eq 0)); exitCode=$bExit; detail=''; artifacts=@($buildOut,$buildErr) }
}

# IB_HEALTHCHECK (release exe)
$hcOut = Join-Path $gateDir 'healthcheck.stdout.log'
$hcErr = Join-Path $gateDir 'healthcheck.stderr.log'
$hcArgs = @('-ProjectRoot',$ProjectRoot,'-ExePath',(Join-Path $ProjectRoot 'x64\Release\HeptaTrader.exe'),'-WorkDir',(Join-Path $ProjectRoot 'x64\Release'),'-TimeoutSec',"$HealthTimeoutSec")
if($NoLaunch){ $hcArgs += '-NoLaunch' }
$checks += Run-PsScript -Name 'IB_HEALTHCHECK' -File (Join-Path $ProjectRoot 'scripts\ib_gateway_healthcheck.ps1') -Args $hcArgs -Stdout $hcOut -Stderr $hcErr -TimeoutSec ($HealthTimeoutSec+20)

# IB_REGRESSION_ROUND
$ibrOut = Join-Path $gateDir 'ib_regression.stdout.log'
$ibrErr = Join-Path $gateDir 'ib_regression.stderr.log'
$ibrArgs = @('-ProjectRoot',$ProjectRoot,'-PythonExe',$PythonExe,'-IbHost',$IbHost,'-Port',"$Port",'-TimeoutSec',"$RegressionTimeoutSec")
$checks += Run-PsScript -Name 'IB_REGRESSION_ROUND' -File (Join-Path $ProjectRoot 'scripts\run_ib_regression_round.ps1') -Args $ibrArgs -Stdout $ibrOut -Stderr $ibrErr -TimeoutSec ($RegressionTimeoutSec+30)

# CTP_REGRESSION_ROUND
$ctpOut = Join-Path $gateDir 'ctp_regression.stdout.log'
$ctpErr = Join-Path $gateDir 'ctp_regression.stderr.log'
$checks += Run-PsScript -Name 'CTP_REGRESSION_ROUND' -File (Join-Path $ProjectRoot 'scripts\run_ctp_regression_round.ps1') -Args @() -Stdout $ctpOut -Stderr $ctpErr -TimeoutSec 120

# RECONCILE_CRITICAL_BLOCK
$recOut = Join-Path $gateDir 'reconcile.stdout.log'
$recErr = Join-Path $gateDir 'reconcile.stderr.log'
$checks += Run-PsScript -Name 'RECONCILE_CRITICAL_BLOCK' -File (Join-Path $ProjectRoot 'scripts\check_reconcile_critical_block.ps1') -Args @() -Stdout $recOut -Stderr $recErr -TimeoutSec 30

$overall = $true
foreach($c in $checks){ if(-not [bool]$c.pass){ $overall = $false } }

$summary = [pscustomobject]@{
  timestamp = $ts
  scope = 'IB+CTP'
  excludes = @('XT')
  checks = $checks
  overall = if($overall){'PASS'} else {'FAIL'}
}
$summary | ConvertTo-Json -Depth 6 | Set-Content $summaryJson -Encoding UTF8

$lines = @()
$lines += "OVERALL=$(if($overall){'PASS'}else{'FAIL'})"
$lines += "EXIT_CODE=$(if($overall){0}else{10})"
$lines += 'SCOPE=IB+CTP (XT excluded)'
$lines += "SUMMARY_JSON=$summaryJson"
$lines += "GATE_DIR=$gateDir"
foreach($c in $checks){
  $lines += "[$($c.name)] pass=$([bool]$c.pass) exit=$($c.exitCode) detail=$($c.detail)"
}
$lines | Set-Content $summaryTxt -Encoding UTF8

Write-Output "OVERALL=$(if($overall){'PASS'}else{'FAIL'})"
Write-Output "EXIT_CODE=$(if($overall){0}else{10})"
Write-Output 'SCOPE=IB+CTP (XT excluded)'
Write-Output "SUMMARY_JSON=$summaryJson"
Write-Output "GATE_DIR=$gateDir"

if($overall){ exit 0 } else { exit 10 }

