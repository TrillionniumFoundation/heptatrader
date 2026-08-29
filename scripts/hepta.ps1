param(
  [ValidateSet('profile','build','monitor','launch','release','help')] [string]$Action = 'help',
  [ValidateSet('dev','rc','paper')] [string]$Phase = 'rc',
  [ValidateSet('pr-smoke','release','nightly')] [string]$SoakProfile,
  [string]$SoakBuildDir,
  [ValidateSet('sim','paper','live')] [string]$ReleaseProfile,
  [ValidateSet('safe','balanced','aggressive')] [string]$Profile = 'balanced',
  [int]$Tail = 300
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$scripts = Join-Path $root 'scripts'
$releaseExe = Join-Path $root 'x64\Release\HeptaTrader.exe'

function Set-Profile([string]$p){
  & powershell -ExecutionPolicy Bypass -File (Join-Path $scripts 'set_strategy_profile.ps1') -Profile $p
}

function Build-Release {
  & powershell -ExecutionPolicy Bypass -File (Join-Path $scripts 'build_release_ib.ps1')
}

function Monitor([int]$tail){
  & powershell -ExecutionPolicy Bypass -File (Join-Path $scripts 'monitor_ib_session.ps1') -Tail $tail
}


function Sync-Config {
  $src = Join-Path $root 'HeptaTrade\HeptaTraderConfig.xml'
  $dst = Join-Path $root 'x64\Release\HeptaTraderConfig.xml'
  if((Test-Path $src) -and (Test-Path (Split-Path $dst))){
    $copy = $false
    if(!(Test-Path $dst)){ $copy = $true }
    else {
      $srcTime=(Get-Item $src).LastWriteTimeUtc
      $dstTime=(Get-Item $dst).LastWriteTimeUtc
      if($srcTime -gt $dstTime){ $copy = $true }
    }
    if($copy){
      Copy-Item -LiteralPath $src -Destination $dst -Force
      Write-Host "[HEPTA-CONFIG] synced $src -> $dst"
    }
  }
}

function Launch {
  if(!(Test-Path $releaseExe)){ throw "Executable not found: $releaseExe" }
  Sync-Config
  Push-Location (Split-Path $releaseExe)
  try { & $releaseExe }
  finally { Pop-Location }
}

function Release-Check {
  $check = Join-Path $scripts 'release_check.ps1'
  if(!(Test-Path $check)){ throw "Missing release check script: $check" }
  $invokeArgs = @('-ProjectRoot', $root, '-Phase', $Phase)
  if(-not [string]::IsNullOrWhiteSpace($ReleaseProfile)){ $invokeArgs += @('-Profile', $ReleaseProfile) }
  if(-not [string]::IsNullOrWhiteSpace($SoakProfile)){ $invokeArgs += @('-SoakProfile', $SoakProfile) }
  if(-not [string]::IsNullOrWhiteSpace($SoakBuildDir)){ $invokeArgs += @('-SoakBuildDir', $SoakBuildDir) }
  & powershell -ExecutionPolicy Bypass -File $check @invokeArgs
  exit $LASTEXITCODE
}

switch($Action){
  'profile' { Set-Profile $Profile }
  'build'   { Build-Release }
  'monitor' { Monitor $Tail }
  'launch'  { Launch }
  'release' { Release-Check }
  default {
@"
Hepta unified entry

Usage:
  powershell -ExecutionPolicy Bypass -File $scripts\hepta.ps1 -Action profile -Profile balanced
  powershell -ExecutionPolicy Bypass -File $scripts\hepta.ps1 -Action build
  powershell -ExecutionPolicy Bypass -File $scripts\hepta.ps1 -Action monitor -Tail 300
  powershell -ExecutionPolicy Bypass -File $scripts\hepta.ps1 -Action launch
  powershell -ExecutionPolicy Bypass -File $scripts\hepta.ps1 -Action release -Phase dev
  powershell -ExecutionPolicy Bypass -File $scripts\hepta.ps1 -Action release -Phase rc
  powershell -ExecutionPolicy Bypass -File $scripts\hepta.ps1 -Action release -Phase paper -SoakBuildDir <build-dir>

Recommended flow:
  1) profile
  2) launch
  3) monitor (in another terminal)
"@ | Write-Host
  }
}
