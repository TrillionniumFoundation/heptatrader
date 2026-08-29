param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [ValidateSet('dev','rc','paper')]
  [string]$Phase = 'rc',
  [string]$ConfigPath,
  [ValidateSet('sim','paper','live')]
  [string]$Profile,
  [string]$PythonExe = 'python',
  [string]$IbHost = '127.0.0.1',
  [int]$Port = 4002,
  [int]$HealthTimeoutSec = 45,
  [int]$RegressionTimeoutSec = 90,
  [switch]$SkipHealthcheck,
  [switch]$SkipRegression,
  [switch]$SkipSystemLatency,
  [switch]$NoLaunch,
  [switch]$Strict,
  [ValidateSet('pr-smoke','release','nightly')]
  [string]$SoakProfile,
  [string]$SoakBuildDir
)

$ErrorActionPreference = 'Stop'

function New-CheckResult([string]$Name){
  [ordered]@{ name = $Name; pass = $false; detail = ''; artifacts = @() }
}

function Add-CheckResult([ref]$Checks, [hashtable]$Result){
  $Checks.Value += $Result
}

function Run-PowerShellFile {
  param(
    [string]$File,
    [string[]]$Args,
    [string]$StdoutPath,
    [string]$StderrPath,
    [int]$TimeoutSec = 0
  )

  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $File) + $Args
  $p = Start-Process -FilePath 'powershell' -ArgumentList $argList -PassThru -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath

  if($TimeoutSec -gt 0){
    $timedOut = -not $p.WaitForExit($TimeoutSec * 1000)
    if($timedOut){
      try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch {}
      return [ordered]@{ exitCode = 124; timedOut = $true }
    }
  } else {
    $p.WaitForExit()
  }

  $exitCode = $p.ExitCode
  if($null -eq $exitCode){ $exitCode = 0 }
  return [ordered]@{ exitCode = [int]$exitCode; timedOut = $false }
}

function Get-LatestDir([string]$Path, [string]$Filter){
  if(!(Test-Path $Path)){ return $null }
  return Get-ChildItem -Path $Path -Directory -Filter $Filter -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
}

function Test-HasIbRiskNodes {
  param([string]$ConfigFile)

  if([string]::IsNullOrWhiteSpace($ConfigFile)){ return $false }
  if(!(Test-Path $ConfigFile)){ return $false }

  try {
    [xml]$xml = Get-Content -LiteralPath $ConfigFile -Raw
    return ($null -ne $xml.Config.IBServer -and $null -ne $xml.Config.IBRisk)
  } catch {
    return $false
  }
}

function Test-IsDisabledFlag {
  param([string]$Raw)
  if([string]::IsNullOrWhiteSpace($Raw)){ return $false }
  $v = $Raw.Trim().ToLowerInvariant()
  return ($v -eq '0' -or $v -eq 'false' -or $v -eq 'off' -or $v -eq 'no')
}

function Test-RiskConfig {
  param([string]$ConfigFile)

  $r = New-CheckResult 'RISK_CONFIG'
  $r.artifacts += $ConfigFile

  if(!(Test-Path $ConfigFile)){
    $r.detail = "Config not found: $ConfigFile"
    return $r
  }

  try {
    [xml]$xml = Get-Content -LiteralPath $ConfigFile -Raw
  } catch {
    $r.detail = "Config XML parse failed: $($_.Exception.Message)"
    return $r
  }

  $ibServer = $xml.Config.IBServer
  $ibRisk = $xml.Config.IBRisk
  $issues = New-Object System.Collections.Generic.List[string]

  if($null -eq $ibServer){ $issues.Add('Missing <IBServer>.') }
  if($null -eq $ibRisk){ $issues.Add('Missing <IBRisk>.') }

  if($ibServer){
    if("$($ibServer.Mode)" -ne 'IB'){ $issues.Add("IBServer.Mode should be 'IB' for IB release gate.") }
    if([string]::IsNullOrWhiteSpace("$($ibServer.Host)")){ $issues.Add('IBServer.Host is empty.') }
    $port = 0
    [void][int]::TryParse("$($ibServer.Port)", [ref]$port)
    if($port -le 0){ $issues.Add('IBServer.Port invalid.') }
    $clientId = 0
    [void][int]::TryParse("$($ibServer.ClientId)", [ref]$clientId)
    if($clientId -lt 1){ $issues.Add('IBServer.ClientId should be >=1.') }
  }

  if($ibRisk){
    $requiredOne = @('RequireTwsConnected','RequireNextValidId','EnableAutoCircuitBreaker','EnableErrorCodeBlacklist')
    foreach($k in $requiredOne){
      if("$($ibRisk.$k)" -ne '1'){ $issues.Add("IBRisk.$k should be 1.") }
    }

    $numRules = @(
      @{ key='MaxOrderQuantity'; min=1; max=1000 },
      @{ key='MaxDailyOrders'; min=1; max=20 },
      @{ key='FuseOnErrorCount'; min=1; max=10 },
      @{ key='MaxPriceDeviationBps'; min=1; max=100 },
      @{ key='DuplicateOrderWindowSec'; min=1; max=60 },
      @{ key='DuplicatePriceTolerance'; min=0.0000001; max=1 }
    )

    foreach($rule in $numRules){
      $raw = "$($ibRisk.($rule.key))"
      $v = 0.0
      if(-not [double]::TryParse($raw, [ref]$v)){
        $issues.Add("IBRisk.$($rule.key) is not numeric.")
        continue
      }
      if($v -lt $rule.min -or $v -gt $rule.max){
        $issues.Add("IBRisk.$($rule.key) out of range [$($rule.min), $($rule.max)] (actual=$raw).")
      }
    }

    if([string]::IsNullOrWhiteSpace("$($ibRisk.ErrorCodeBlacklist)")){
      $issues.Add('IBRisk.ErrorCodeBlacklist is empty.')
    }
  }

  if($issues.Count -eq 0){
    $r.pass = $true
    $r.detail = 'IBServer/IBRisk mandatory fields and ranges are valid.'
  } else {
    $r.pass = $false
    $r.detail = ($issues -join ' ')
  }

  return $r
}

$runtimeRoot = Join-Path $ProjectRoot 'runtime-logs'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$gateDir = Join-Path $runtimeRoot ("release-check-$ts")
New-Item -ItemType Directory -Path $gateDir -Force | Out-Null

$resolveScript = Join-Path $ProjectRoot 'scripts\resolve_hepta_config.py'
if(!(Test-Path $resolveScript)){
  throw "Missing resolver script: $resolveScript"
}

$pyCmd = Get-Command $PythonExe -ErrorAction SilentlyContinue
if(-not $pyCmd){
  $pyCmd = Get-Command py -ErrorAction SilentlyContinue
  if(-not $pyCmd){ throw "Python not found. Please install Python or pass -PythonExe." }
  $PythonExe = 'py'
}

$resolveArgs = @()
if($PythonExe -eq 'py'){ $resolveArgs += '-3' }
# A phase supplies safe defaults while still allowing an explicit profile. The
# dev phase is intentionally offline; rc/paper use paper unless overridden.
if([string]::IsNullOrWhiteSpace($Profile)) {
  $Profile = if($Phase -eq 'dev') { 'sim' } else { 'paper' }
}
if($Phase -eq 'dev' -and [string]::IsNullOrWhiteSpace($ConfigPath) -and
   [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('HEPTA_CONFIG_PATH')) -and
   [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('HEPTA_TRADER_CONFIG_PATH'))) {
  $ConfigPath = Join-Path $ProjectRoot 'HeptaTrade\HeptaTraderConfig.xml.example'
}
$resolveArgs += @($resolveScript, '--project-root', $ProjectRoot)
if(-not [string]::IsNullOrWhiteSpace($ConfigPath)){ $resolveArgs += @('--config', $ConfigPath) }
if(-not [string]::IsNullOrWhiteSpace($Profile)){ $resolveArgs += @('--profile', $Profile) }

$resolveRaw = & $PythonExe @resolveArgs
if($LASTEXITCODE -ne 0){ throw 'resolve_hepta_config.py failed.' }
$resolved = $resolveRaw | ConvertFrom-Json

$ConfigPath = $resolved.config_path
$env:HEPTA_CONFIG_PATH = $resolved.config_path
$env:HEPTA_PROFILE = $resolved.profile
$env:HEPTA_CONFIG_SHA256 = $resolved.sha256
Write-Host ("CONFIG_FINGERPRINT config_path={0} profile={1} sha256={2}" -f $resolved.config_path, $resolved.profile, $resolved.sha256)

# Run static checks exactly once and fold the result into the phase summary.
# CI callers should consume this record instead of invoking duplicate checks.
$static = New-CheckResult 'STATIC_CHECKS'
$staticLog = Join-Path $gateDir 'static-checks.log'
try {
  $env:PYTHONDONTWRITEBYTECODE = '1'
  $staticArgs = @()
  if($PythonExe -eq 'py'){ $staticArgs += '-3' }
  $staticArgs += @('-m', 'compileall', '-q', (Join-Path $ProjectRoot 'scripts'))
  & $PythonExe @staticArgs *> $staticLog
  $static.pass = ($LASTEXITCODE -eq 0)
  $static.detail = if($static.pass){ 'python compileall passed (single invocation).' } else { "python compileall exitCode=$LASTEXITCODE" }
} catch {
  $static.pass = $false
  $static.detail = "static check failed: $($_.Exception.Message)"
}
$static.artifacts += $staticLog
$checks = @($static)

if($Phase -eq 'paper' -and ($SkipHealthcheck -or $SkipRegression -or $SkipSystemLatency)) {
  throw 'paper phase forbids SkipHealthcheck, SkipRegression, and SkipSystemLatency; mandatory PAPER gates must run.'
}
if($Phase -in @('rc','paper') -and $SoakProfile -eq 'pr-smoke') {
  throw 'pr-smoke is diagnostic only; rc/paper compatibility gates require the release or nightly soak profile.'
}

# dev is fast local feedback; rc and paper retain the full existing gates.
$runHealthcheck = (-not $SkipHealthcheck) -and ($Phase -ne 'dev')
$runRegression = (-not $SkipRegression) -and ($Phase -ne 'dev')
$runSystemLatency = (-not $SkipSystemLatency) -and ($Phase -ne 'dev')
$recommendations = New-Object System.Collections.Generic.List[string]

if($runHealthcheck){
  $health = New-CheckResult 'IB_HEALTHCHECK'
  $healthStdout = Join-Path $gateDir 'healthcheck.stdout.log'
  $healthStderr = Join-Path $gateDir 'healthcheck.stderr.log'
  $healthScript = Join-Path $ProjectRoot 'scripts\ib_gateway_healthcheck.ps1'

  if(Test-Path $healthScript){
    $args = @('-ProjectRoot', $ProjectRoot, '-TimeoutSec', "$HealthTimeoutSec")
    if($NoLaunch){ $args += '-NoLaunch' }
    $run = Run-PowerShellFile -File $healthScript -Args $args -StdoutPath $healthStdout -StderrPath $healthStderr -TimeoutSec ($HealthTimeoutSec + 30)

    $health.artifacts += @($healthStdout, $healthStderr)
    $latestHealth = Get-LatestDir -Path $runtimeRoot -Filter 'ib-healthcheck-*'
    if($latestHealth){
      $health.artifacts += $latestHealth.FullName
      $summaryJson = Join-Path $latestHealth.FullName 'summary.json'
      if(Test-Path $summaryJson){
        $health.artifacts += $summaryJson
        try {
          $s = Get-Content -LiteralPath $summaryJson -Raw | ConvertFrom-Json
          $health.pass = ($s.overall -eq 'PASS')
          $health.detail = "overall=$($s.overall); connectivity=$($s.checks.connectivity.detail); nextValidId=$($s.checks.nextValidId.detail); usdCnhTick=$($s.checks.usdCnhTick.detail)"
        } catch {
          $health.pass = ($run.exitCode -eq 0)
          $health.detail = "summary parse failed; exitCode=$($run.exitCode)"
        }
      } else {
        $health.pass = ($run.exitCode -eq 0)
        $health.detail = "summary missing; exitCode=$($run.exitCode)"
      }
    } else {
      $health.pass = ($run.exitCode -eq 0)
      $health.detail = "artifact dir missing; exitCode=$($run.exitCode)"
    }

    if($run.timedOut){
      $health.pass = $false
      $health.detail = 'healthcheck timed out.'
    }
  } else {
    $health.detail = "Missing script: $healthScript"
  }

  if(-not $health.pass){ $recommendations.Add('Fix IB health: paper login/API port/localhost access, and ensure nextValidId + USD/CNH tick appear.') }
  Add-CheckResult ([ref]$checks) $health
}

if($runRegression){
  $reg = New-CheckResult 'IB_REGRESSION_ROUND'
  $regStdout = Join-Path $gateDir 'regression.stdout.log'
  $regStderr = Join-Path $gateDir 'regression.stderr.log'
  $regScript = Join-Path $ProjectRoot 'scripts\run_ib_regression_round.ps1'

  if(Test-Path $regScript){
    $args = @(
      '-ProjectRoot', $ProjectRoot,
      '-IbHost', $IbHost,
      '-Port', "$Port",
      '-TimeoutSec', "$RegressionTimeoutSec",
      '-PythonExe', $PythonExe
    )

    $run = Run-PowerShellFile -File $regScript -Args $args -StdoutPath $regStdout -StderrPath $regStderr -TimeoutSec ($RegressionTimeoutSec + 60)
    $reg.artifacts += @($regStdout, $regStderr)

    $latestRoundRoot = Join-Path $runtimeRoot 'ib-regression-round'
    $latestRound = Get-LatestDir -Path $latestRoundRoot -Filter '*'
    if($latestRound){
      $reg.artifacts += $latestRound.FullName
      $reportJson = Join-Path $latestRound.FullName 'round_report.json'
      if(Test-Path $reportJson){
        $reg.artifacts += $reportJson
        try {
          $r = Get-Content -LiteralPath $reportJson -Raw | ConvertFrom-Json
          $reg.pass = ($r.overall -eq 'PASS')
          $reg.detail = "overall=$($r.overall); orderFinal=$($r.order.finalStatus); orderLoop=$($r.checks.orderCancelLoop.detail)"
        } catch {
          $reg.pass = ($run.exitCode -eq 0)
          $reg.detail = "report parse failed; exitCode=$($run.exitCode)"
        }
      } else {
        $reg.pass = ($run.exitCode -eq 0)
        $reg.detail = "round report missing; exitCode=$($run.exitCode)"
      }
    } else {
      $reg.pass = ($run.exitCode -eq 0)
      $reg.detail = "round dir missing; exitCode=$($run.exitCode)"
    }

    if($run.timedOut){
      $reg.pass = $false
      $reg.detail = 'regression round timed out.'
    }
  } else {
    $reg.detail = "Missing script: $regScript"
  }

  if(-not $reg.pass){ $recommendations.Add('Fix regression round: require complete place->status->cancel->final status loop and inspect runner.stderr.log + order_loop.jsonl.') }
  Add-CheckResult ([ref]$checks) $reg
}



$systemLatencyMandatory = ($resolved.profile -eq 'paper' -or $resolved.profile -eq 'live')
if((-not $runSystemLatency) -and $systemLatencyMandatory -and $Phase -ne 'dev'){
  $sysSkip = New-CheckResult 'SYSTEM_LOW_LATENCY'
  $sysSkip.pass = $false
  $sysSkip.detail = 'SkipSystemLatency is blocked for paper/live profile.'
  Add-CheckResult ([ref]$checks) $sysSkip
  $recommendations.Add('Do not skip system low-latency checks for paper/live release gates.')

  $coloSkip = New-CheckResult 'IB_COLOCATION'
  $coloSkip.pass = $false
  $coloSkip.detail = 'SkipSystemLatency is blocked for paper/live profile.'
  Add-CheckResult ([ref]$checks) $coloSkip
}

if($runSystemLatency){
  $sys = New-CheckResult 'SYSTEM_LOW_LATENCY'
  $sysStdout = Join-Path $gateDir 'system-latency.stdout.log'
  $sysStderr = Join-Path $gateDir 'system-latency.stderr.log'
  $sysScript = Join-Path $ProjectRoot 'scripts\optimize_ib_host_latency.ps1'
  if(Test-Path $sysScript){
    $run = Run-PowerShellFile -File $sysScript -Args @('-IbHost', $IbHost, '-IbPort', "$Port") -StdoutPath $sysStdout -StderrPath $sysStderr -TimeoutSec 60
    $sys.artifacts += @($sysStdout, $sysStderr)
    $latencyReport = Join-Path $ProjectRoot 'runtime-logs\host_latency_tuning_report.json'
    if(Test-Path $latencyReport){
      $sys.artifacts += $latencyReport
      try {
        $rep = Get-Content -LiteralPath $latencyReport -Raw | ConvertFrom-Json
        $nicCount = @($rep.ActiveNic).Count
        $coloPass = [bool]$rep.ColocationCheck.Pass
        $sys.pass = ($run.exitCode -eq 0 -and $nicCount -ge 1 -and $coloPass)
        $sys.detail = "exitCode=$($run.exitCode); activeNic=$nicCount; apply=$($rep.Apply); colocation=$coloPass"
      } catch {
        $sys.pass = ($run.exitCode -eq 0)
        $sys.detail = "report parse failed; exitCode=$($run.exitCode)"
      }
    } else {
      $sys.pass = $false
      $sys.detail = 'host_latency_tuning_report.json missing'
    }
    if($run.timedOut){
      $sys.pass = $false
      $sys.detail = 'system latency audit timed out.'
    }
  } else {
    $sys.detail = "Missing script: $sysScript"
  }

  if(-not $sys.pass){ $recommendations.Add('Fix system low-latency baseline (power/NIC/affinity/colocation audit) before release.') }
  Add-CheckResult ([ref]$checks) $sys

  $colo = New-CheckResult 'IB_COLOCATION'
  $coloStdout = Join-Path $gateDir 'ib-colocation.stdout.log'
  $coloStderr = Join-Path $gateDir 'ib-colocation.stderr.log'
  $coloScript = Join-Path $ProjectRoot 'scripts\check_ib_colocation.ps1'
  if(Test-Path $coloScript){
    $runColo = Run-PowerShellFile -File $coloScript -Args @('-IbHost', $IbHost, '-Port', "$Port") -StdoutPath $coloStdout -StderrPath $coloStderr -TimeoutSec 30
    $colo.artifacts += @($coloStdout, $coloStderr)
    $coloReport = Join-Path $ProjectRoot 'runtime-logs\ib_colocation_check.json'
    if(Test-Path $coloReport){
      $colo.artifacts += $coloReport
      try {
        $cr = Get-Content -LiteralPath $coloReport -Raw | ConvertFrom-Json
        $colo.pass = [bool]$cr.pass
        $colo.detail = "loopback=$($cr.isLoopbackHost); ibProc=$($cr.ibGatewayProcessCount); strategyProc=$($cr.strategyProcessCount); listen=$($cr.localPortListening)"
      } catch {
        $colo.pass = ($runColo.exitCode -eq 0)
        $colo.detail = "colocation report parse failed; exitCode=$($runColo.exitCode)"
      }
    } else {
      $colo.pass = ($runColo.exitCode -eq 0)
      $colo.detail = "colocation report missing; exitCode=$($runColo.exitCode)"
    }
    if($runColo.timedOut){
      $colo.pass = $false
      $colo.detail = 'colocation check timed out.'
    }
  } else {
    $colo.detail = "Missing script: $coloScript"
  }

  if(-not $colo.pass){ $recommendations.Add('Fix colocation baseline: use loopback IB host and run gateway + strategy on same low-latency host.') }
  Add-CheckResult ([ref]$checks) $colo
}

if($resolved.profile -eq 'sim' -and -not (Test-HasIbRiskNodes -ConfigFile $ConfigPath)){
  $risk = New-CheckResult 'RISK_CONFIG'
  $risk.pass = $true
  $risk.detail = 'skipped for sim profile without IBServer/IBRisk nodes'
  $risk.artifacts += $ConfigPath
  Add-CheckResult ([ref]$checks) $risk
} else {
  $risk = Test-RiskConfig -ConfigFile $ConfigPath
  if(-not $risk.pass){
    $recommendations.Add('Fix IBRisk config: enable key guards (RequireTwsConnected/RequireNextValidId/circuit-breaker/error blacklist) and keep thresholds conservative.')
  }
  Add-CheckResult ([ref]$checks) $risk
}

$execMode = New-CheckResult 'IB_EXECUTION_MODE'
if($resolved.profile -eq 'paper' -or $resolved.profile -eq 'live'){
  $execRaw = [Environment]::GetEnvironmentVariable('HEPTA_IB_EXEC_WORKER_THREAD','User')
  $ingestRaw = [Environment]::GetEnvironmentVariable('HEPTA_IB_EVENT_INGEST_THREAD','User')
  $execDisabled = Test-IsDisabledFlag -Raw $execRaw
  $ingestDisabled = Test-IsDisabledFlag -Raw $ingestRaw
  $execMode.pass = (-not $execDisabled) -and (-not $ingestDisabled)
  $execMode.detail = "exec_worker_user='$execRaw' ingest_thread_user='$ingestRaw'"
  if(-not $execMode.pass){
    $recommendations.Add('Enable low-latency execution mode: HEPTA_IB_EXEC_WORKER_THREAD=1 and HEPTA_IB_EVENT_INGEST_THREAD=1 for paper/live release.')
  }
} else {
  $execMode.pass = $true
  $execMode.detail = 'skipped for non-paper/live profile'
}
Add-CheckResult ([ref]$checks) $execMode

if($Phase -eq 'paper') {
  $soak = New-CheckResult 'PAPER_SOAK'
  $soakLog = Join-Path $gateDir 'paper-soak.log'
  $soakProfileEffective = if([string]::IsNullOrWhiteSpace($SoakProfile)){ 'release' } else { $SoakProfile }
  $soakReport = Join-Path $gateDir 'paper-soak.json'
  if([string]::IsNullOrWhiteSpace($SoakBuildDir)) {
    $soak.pass = $false
    $soak.detail = 'SoakBuildDir is required for paper phase; provide a fresh Release build tree.'
    $recommendations.Add('Provide a fresh Release build tree and run the full PAPER soak before promotion.')
  } else { try {
    $soakArgs = @()
    if($PythonExe -eq 'py'){ $soakArgs += '-3' }
    $soakArgs += @(
      (Join-Path $ProjectRoot 'scripts\run_execution_gateway_soak.py'),
      '--build-dir', $SoakBuildDir,
      '--soak-profile', $soakProfileEffective,
      '--require-build-type', 'Release',
      '--report', $soakReport
    )
    & $PythonExe @soakArgs *> $soakLog
    $soakExit = $LASTEXITCODE
    $soak.pass = ($soakExit -eq 0)
    $soak.detail = "profile=$soakProfileEffective; exitCode=$soakExit"
    if(Test-Path $soakReport){
      $soak.artifacts += $soakReport
      try {
        $sr = Get-Content -LiteralPath $soakReport -Raw | ConvertFrom-Json
        $soak.pass = ($soakExit -eq 0 -and [bool]$sr.passed)
        $soak.detail = "profile=$($sr.soak_profile); rounds=$($sr.completed_rounds)/$($sr.requested_rounds); passed=$($sr.passed)"
      } catch { $soak.detail += '; report parse failed' }
    }
  } catch { $soak.pass = $false; $soak.detail = "paper soak failed: $($_.Exception.Message)" } }
  $soak.artifacts += $soakLog
  Add-CheckResult ([ref]$checks) $soak
}

$logCheck = New-CheckResult 'CRITICAL_LOGS'
$fatalPatterns = @(
  'IB preflight check failed',
  'placeOrder failed',
  'Fatal API errors detected',
  'Load Config File Failed',
  'connect failed',
  'Timed out waiting final status',
  'error\s+id=.*\s+code=(502|504|1100|1101|1102|1300)'
)

$logFiles = @()
$logFiles += Get-ChildItem -Path $gateDir -Recurse -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
$latestHealthDir = Get-LatestDir -Path $runtimeRoot -Filter 'ib-healthcheck-*'
if($latestHealthDir){
  $logFiles += Get-ChildItem -Path $latestHealthDir.FullName -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
}
$latestRoundDir = Get-LatestDir -Path (Join-Path $runtimeRoot 'ib-regression-round') -Filter '*'
if($latestRoundDir){
  $logFiles += Get-ChildItem -Path $latestRoundDir.FullName -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
}

$logFiles = $logFiles | Sort-Object -Unique
$logCheck.artifacts += $logFiles
$hits = New-Object System.Collections.Generic.List[string]
foreach($f in $logFiles){
  try {
    $raw = Get-Content -LiteralPath $f -Raw -ErrorAction Stop
    foreach($p in $fatalPatterns){
      if($raw -match $p){
        $hits.Add("$([IO.Path]::GetFileName($f)) :: pattern '$p'")
      }
    }
  } catch {}
}

if($hits.Count -eq 0){
  $logCheck.pass = $true
  $logCheck.detail = 'No critical error markers detected in collected logs.'
} else {
  $logCheck.pass = $false
  $logCheck.detail = ($hits -join '; ')
  $recommendations.Add('Clean critical log errors before release (IB preflight/placeOrder failures, timeout, and connection errors 502/110x).')
}
Add-CheckResult ([ref]$checks) $logCheck

$failed = @($checks | Where-Object { -not $_.pass })
$overallPass = ($failed.Count -eq 0)

if($overallPass){
  $releaseAdvice = 'RELEASABLE: all gates passed (healthcheck, regression, risk config, critical logs).'
} else {
  $releaseAdvice = 'NOT RELEASABLE: one or more release gates failed. Fix then rerun.'
}

if($Strict -and -not $overallPass){
  $releaseAdvice += ' [Strict mode blocks release on any failed gate]'
}

$summary = [ordered]@{
  timestamp = (Get-Date).ToString('o')
  projectRoot = $ProjectRoot
  phase = $Phase
  soakProfile = if([string]::IsNullOrWhiteSpace($SoakProfile)){ $null } else { $SoakProfile }
  configPath = $ConfigPath
  profile = $resolved.profile
  configSha256 = $resolved.sha256
  overall = if($overallPass){ 'PASS' } else { 'FAIL' }
  releaseAdvice = $releaseAdvice
  checks = $checks
  recommendations = @($recommendations | Select-Object -Unique)
  artifacts = [ordered]@{ gateDir = $gateDir }
}

$summaryJson = Join-Path $gateDir 'release_check.json'
$summaryTxt = Join-Path $gateDir 'release_check.txt'

$summary | ConvertTo-Json -Depth 8 | Out-File -FilePath $summaryJson -Encoding utf8

$lines = @()
$lines += "OVERALL=$($summary.overall)"
$lines += "PHASE=$Phase"
$lines += "RELEASE_ADVICE=$($summary.releaseAdvice)"
$lines += "CONFIG_FINGERPRINT config_path=$($summary.configPath) profile=$($summary.profile) sha256=$($summary.configSha256)"
foreach($c in $checks){
  $tag = if($c.pass){ 'PASS' } else { 'FAIL' }
  $lines += ("{0}={1} :: {2}" -f $c.name, $tag, $c.detail)
}
if($summary.recommendations.Count -gt 0){
  $lines += 'RECOMMENDATIONS:'
  foreach($r in $summary.recommendations){ $lines += ("- " + $r) }
}
$lines += "ARTIFACT_DIR=$gateDir"
$lines -join "`r`n" | Out-File -FilePath $summaryTxt -Encoding utf8

Write-Host "OVERALL=$($summary.overall)" -ForegroundColor $(if($overallPass){ 'Green' } else { 'Red' })
Write-Host "RELEASE_ADVICE=$($summary.releaseAdvice)"
Write-Host "SUMMARY_JSON=$summaryJson"
Write-Host "SUMMARY_TXT=$summaryTxt"

if($overallPass){ exit 0 }
exit 1
