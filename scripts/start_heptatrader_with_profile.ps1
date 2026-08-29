param(
  [ValidateSet('IB','CTP','XT')] [string]$Venue,
  [switch]$NoPrompt
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$exe = Join-Path $root 'x64\Release\HeptaTrader.exe'
$profilesPath = Join-Path $root 'scripts\hepta_env_profiles.ps1'

if(!(Test-Path $exe)){ throw "Executable not found: $exe" }
if(!(Test-Path $profilesPath)){ throw "Profile file missing: $profilesPath" }

. $profilesPath

function Apply-Env([hashtable]$h){
  foreach($kv in $h.GetEnumerator()){
    Set-Item -Path ("Env:" + $kv.Key) -Value ([string]$kv.Value)
  }
}

if(-not $Venue){
  Write-Host ''
  Write-Host 'Select trading venue profile:'
  Write-Host '  1) IB'
  Write-Host '  2) CTP'
  Write-Host '  3) XT'
  $opt = Read-Host 'Enter 1/2/3'
  switch($opt){
    '1' { $Venue='IB' }
    '2' { $Venue='CTP' }
    '3' { $Venue='XT' }
    default { throw 'Invalid option. Use 1/2/3.' }
  }
}

Apply-Env $COMMON
Apply-Env (Get-Variable -Name $Venue -ValueOnly)

Write-Host "Loaded env profile: $Venue"
Write-Host "Starting: $exe"

Push-Location (Split-Path $exe)
try {
  if($NoPrompt){
    & $exe
  } else {
    & $exe
  }
}
finally {
  Pop-Location
}
