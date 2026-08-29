param(
  [ValidateSet('DryRun','Apply')]
  [string]$Mode = 'DryRun',
  [switch]$Apply,
  [switch]$IncludeNetAdapterAdvanced,
  [string]$IbGatewayProcess = 'ibgateway',
  [string]$StrategyProcess = 'HeptaDemoStrategyTrader',
  [string]$AffinityMaskHex = '0x0000000F',
  [string]$IbHost = '127.0.0.1',
  [int]$IbPort = 4002
)

$ErrorActionPreference = 'Stop'
if($Apply){ $Mode = 'Apply' }
$applyMode = ($Mode -eq 'Apply')

function Get-ActivePowerPlan {
  (powercfg -GetActiveScheme | Out-String).Trim()
}

function Get-PowerSettingValue {
  param([string]$Subgroup,[string]$Setting)
  try {
    $raw = powercfg /Q SCHEME_CURRENT $Subgroup $Setting | Out-String
    $ac = ($raw | Select-String -Pattern 'Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)' -AllMatches).Matches
    $dc = ($raw | Select-String -Pattern 'Current DC Power Setting Index:\s*0x([0-9a-fA-F]+)' -AllMatches).Matches
    return [ordered]@{
      acHex = if($ac.Count -gt 0){ '0x' + $ac[0].Groups[1].Value } else { $null }
      dcHex = if($dc.Count -gt 0){ '0x' + $dc[0].Groups[1].Value } else { $null }
    }
  } catch {
    return [ordered]@{ acHex = $null; dcHex = $null; error = $_.Exception.Message }
  }
}

function Set-NicLowLatencyProfile {
  param([string]$NicName,[bool]$ApplyMode)

  $result = [ordered]@{ Name = $NicName; Changes = @(); Notes = @() }
  $targets = @(
    @{ DisplayName='Interrupt Moderation'; DisabledValues=@('Disabled','Off') },
    @{ DisplayName='Energy-Efficient Ethernet'; DisabledValues=@('Disabled','Off') },
    @{ DisplayName='EEE'; DisabledValues=@('Disabled','Off') },
    @{ DisplayName='Green Ethernet'; DisabledValues=@('Disabled','Off') },
    @{ DisplayName='Flow Control'; DisabledValues=@('Disabled','Off') }
  )

  foreach($t in $targets){
    $c = [ordered]@{ Property=$t.DisplayName; Found=$false; Before=$null; After=$null; Status='skipped' }
    try {
      $props = Get-NetAdapterAdvancedProperty -Name $NicName -ErrorAction Stop
      $p = $props | Where-Object { $_.DisplayName -eq $t.DisplayName } | Select-Object -First 1
      if($p){
        $c.Found = $true
        $c.Before = $p.DisplayValue
        if($ApplyMode){
          $applied = $false
          foreach($v in $t.DisabledValues){
            try {
              Set-NetAdapterAdvancedProperty -Name $NicName -DisplayName $t.DisplayName -DisplayValue $v -NoRestart -ErrorAction Stop
              $c.After = $v
              $c.Status = 'applied'
              $applied = $true
              break
            } catch {}
          }
          if(-not $applied){ $c.Status = 'unsupported-value'; $c.After = $c.Before }
        } else {
          $c.Status = 'dry-run'
          $c.After = $c.Before
        }
      } else {
        $c.Status = 'unsupported-property'
      }
    } catch {
      $c.Status = 'query-failed'
      $c.After = $c.Before
    }
    $result.Changes += $c
  }

  if($IncludeNetAdapterAdvanced){
    try { $result.Advanced = @(Get-NetAdapterAdvancedProperty -Name $NicName | Select-Object DisplayName,DisplayValue) } catch {}
  }

  return $result
}

function Get-ProcessorState {
  param([string]$ProcessName, [int64]$AffinityMask, [bool]$ApplyMode)
  $rows = @()
  $procs = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue
  foreach($proc in $procs){
    $beforeAffinity = ('0x{0:X}' -f [int64]$proc.ProcessorAffinity)
    $beforePriority = "$($proc.PriorityClass)"
    $status = 'dry-run'

    if($ApplyMode){
      try { $proc.ProcessorAffinity = $AffinityMask } catch { $status = 'affinity-failed' }
      try { $proc.PriorityClass = 'High' } catch { if($status -eq 'affinity-failed'){ $status = 'partial' } else { $status = 'priority-failed' } }
      if($status -eq 'dry-run'){ $status = 'applied' }
    }

    $rows += [ordered]@{
      Name = $proc.ProcessName
      Id = $proc.Id
      BeforeAffinity = $beforeAffinity
      AfterAffinity = ('0x{0:X}' -f [int64]$proc.ProcessorAffinity)
      BeforePriority = $beforePriority
      AfterPriority = "$($proc.PriorityClass)"
      Status = $status
    }
  }
  return $rows
}

function Test-Colocation {
  param([string]$IbHost,[int]$IbPort,[string]$IbGatewayProcess,[string]$StrategyProcess)

  $hostNormalized = $IbHost.Trim().ToLowerInvariant()
  $loopbackHosts = @('127.0.0.1','localhost','::1')
  $isLoopback = $loopbackHosts -contains $hostNormalized

  $ibProc = @(Get-Process -Name $IbGatewayProcess -ErrorAction SilentlyContinue)
  $stProc = @(Get-Process -Name $StrategyProcess -ErrorAction SilentlyContinue)

  $portBoundLocal = $false
  try {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $IbPort -ErrorAction Stop
    if(@($listeners).Count -gt 0){ $portBoundLocal = $true }
  } catch {}

  [ordered]@{
    IbHost = $IbHost
    IbPort = $IbPort
    IsLoopbackHost = $isLoopback
    LocalPortListening = $portBoundLocal
    IbGatewayProcessCount = $ibProc.Count
    StrategyProcessCount = $stProc.Count
    Pass = ($isLoopback -and ($ibProc.Count -ge 1) -and ($stProc.Count -ge 1))
    Detail = if($isLoopback){
      'IB host is loopback; process counts indicate local colocation state.'
    } else {
      'IB host is remote/non-loopback; strategy and gateway are not colocated on same host by configuration.'
    }
  }
}

$report = [ordered]@{}
$report.Timestamp = (Get-Date).ToString('o')
$report.Host = $env:COMPUTERNAME
$report.Mode = $Mode
$report.Apply = [bool]$applyMode
$report.CpuCount = [Environment]::ProcessorCount
$report.AffinityMaskHex = $AffinityMaskHex
$report.IbConnectivityTarget = [ordered]@{ Host=$IbHost; Port=$IbPort }
$report.Verification = [ordered]@{}

$powerPlans = powercfg -L
$report.PowerPlans = @($powerPlans)
$report.Verification.PowerPlanBefore = Get-ActivePowerPlan

$highPerf = ($powerPlans | Select-String -Pattern 'High performance|高性能').Line
if($applyMode -and $highPerf -and ($highPerf -match '([a-fA-F0-9-]{36})')){
  powercfg -S $matches[1] | Out-Null
  $report.PowerPlanSet = $matches[1]
}
$report.Verification.PowerPlanAfter = Get-ActivePowerPlan

$subProc = '54533251-82be-4824-96c1-47b60b740d00'
$setMin = '893dee8e-2bef-41e0-89c6-b55d0929964c'
$setMax = 'bc5038f7-23e0-4960-96da-33abaf5935ec'
$setCoreMin = '0cc5b647-c1df-4637-891a-dec35c318583'
$setCoreMax = 'ea062031-0e34-4ff1-9b6d-eb1059334028'

$report.PowerGovernance = [ordered]@{
  ProcessorMinPercent = Get-PowerSettingValue -Subgroup $subProc -Setting $setMin
  ProcessorMaxPercent = Get-PowerSettingValue -Subgroup $subProc -Setting $setMax
  CoreParkingMinCores = Get-PowerSettingValue -Subgroup $subProc -Setting $setCoreMin
  CoreParkingMaxCores = Get-PowerSettingValue -Subgroup $subProc -Setting $setCoreMax
  Guidance = @(
    'Execution host guidance: processor min/max AC to 100%, core parking min/max to 100% when thermals allow.',
    'Turbo guidance: keep turbo enabled unless thermal throttling causes unstable latency; validate with benchmark before disabling.'
  )
}

$adapterInfo = Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object Name, InterfaceDescription, LinkSpeed, Status
$report.ActiveNic = @($adapterInfo)
$nicTuningResults = @()
foreach($nic in $adapterInfo){
  $nicTuningResults += Set-NicLowLatencyProfile -NicName $nic.Name -ApplyMode:$applyMode
}
$report.NicTuning = $nicTuningResults

$affinityMask = [int64]$AffinityMaskHex
$affApplied = @()
$affApplied += Get-ProcessorState -ProcessName $IbGatewayProcess -AffinityMask $affinityMask -ApplyMode:$applyMode
$affApplied += Get-ProcessorState -ProcessName $StrategyProcess -AffinityMask $affinityMask -ApplyMode:$applyMode
$report.ProcessTuning = $affApplied

$report.ColocationCheck = Test-Colocation -IbHost $IbHost -IbPort $IbPort -IbGatewayProcess $IbGatewayProcess -StrategyProcess $StrategyProcess

$report.Verification.Commands = @(
  'powercfg -GetActiveScheme',
  'powercfg /Q SCHEME_CURRENT SUB_PROCESSOR',
  "Get-Process -Name $IbGatewayProcess,$StrategyProcess | Select Name,Id,PriorityClass,ProcessorAffinity",
  'Get-NetAdapter | ? Status -eq Up | Select Name,Status,LinkSpeed',
  "powershell -ExecutionPolicy Bypass -File scripts/check_ib_colocation.ps1 -IbHost $IbHost -Port $IbPort"
)

$logDir = Join-Path $PSScriptRoot '..\runtime-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$outPath = Join-Path $logDir 'host_latency_tuning_report.json'
$report | ConvertTo-Json -Depth 10 | Set-Content $outPath -Encoding UTF8
Write-Host "Report written: $outPath"
Write-Host "Mode=$Mode Apply=$applyMode"

