param(
  [string]$ProbeExe,
  [string]$Host = "127.0.0.1",
  [int[]]$Ports = @(7497, 4002, 7496, 4001),
  [int]$ClientIdStart = 101
)

$ErrorActionPreference = 'Stop'

if([string]::IsNullOrWhiteSpace($ProbeExe)){
  throw "Please pass -ProbeExe <full path to ib_connection_probe.exe>"
}
if(!(Test-Path $ProbeExe)){
  throw "Probe executable not found: $ProbeExe"
}

Write-Host "== IB/TWS Port Probe ==" -ForegroundColor Cyan
Write-Host "ProbeExe: $ProbeExe"
Write-Host "Host: $Host"
Write-Host "Ports: $($Ports -join ', ')"

$ok = $false
$idx = 0
foreach($p in $Ports){
  $cid = $ClientIdStart + $idx
  $idx++
  Write-Host "\n[TRY] host=$Host port=$p clientId=$cid" -ForegroundColor Yellow

  $proc = Start-Process -FilePath $ProbeExe -ArgumentList @($Host, "$p", "$cid") -NoNewWindow -Wait -PassThru
  if($proc.ExitCode -eq 0){
    Write-Host "[ OK ] Connected on port $p" -ForegroundColor Green
    $ok = $true
    break
  } else {
    Write-Host "[FAIL] ExitCode=$($proc.ExitCode) on port $p" -ForegroundColor Red
  }
}

if(-not $ok){
  Write-Host "\nNo IB endpoint responded successfully. Check TWS/Gateway login/API settings/firewall." -ForegroundColor Red
  exit 1
}

Write-Host "\nIB connectivity probe succeeded." -ForegroundColor Green
exit 0
