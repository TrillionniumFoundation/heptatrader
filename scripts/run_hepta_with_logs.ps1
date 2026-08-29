param(
  [ValidateSet('trader','simulator')]
  [string]$Mode = 'trader',
  [string]$ConfigPath,
  [ValidateSet('sim','paper','live')]
  [string]$Profile,
  [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'
$root = 'D:\quant\HeptaTrader-master'
$logDir = Join-Path $root 'runtime-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$ts = Get-Date -Format 'yyyyMMdd-HHmmss'

$resolveScript = Join-Path $root 'scripts\resolve_hepta_config.py'
if(!(Test-Path $resolveScript)){ throw "Missing resolver: $resolveScript" }

$pyCmd = Get-Command $PythonExe -ErrorAction SilentlyContinue
if(-not $pyCmd){
  $pyCmd = Get-Command py -ErrorAction SilentlyContinue
  if(-not $pyCmd){ throw "Python not found. Please install Python or pass -PythonExe." }
  $PythonExe = 'py'
}

$resolveArgs = @()
if($PythonExe -eq 'py'){ $resolveArgs += '-3' }
$resolveArgs += @($resolveScript, '--project-root', $root)
if($ConfigPath){ $resolveArgs += @('--config', $ConfigPath) }
if($Profile){ $resolveArgs += @('--profile', $Profile) }

$resolveRaw = & $PythonExe @resolveArgs
if($LASTEXITCODE -ne 0){ throw "resolve_hepta_config.py failed." }
$resolved = $resolveRaw | ConvertFrom-Json

$env:HEPTA_CONFIG_PATH = $resolved.config_path
$env:HEPTA_PROFILE = $resolved.profile
$env:HEPTA_CONFIG_SHA256 = $resolved.sha256

Write-Host ("CONFIG_FINGERPRINT config_path={0} profile={1} sha256={2}" -f $resolved.config_path, $resolved.profile, $resolved.sha256)

if($Mode -eq 'trader'){
  $exe = Join-Path $root 'x64\Debug\HeptaTrader.exe'
  $wd  = Join-Path $root 'x64\Debug'
} else {
  $exe = Join-Path $root 'HeptaSimulator\x64\Debug\HeptaSimulator.exe'
  $wd  = Join-Path $root 'HeptaSimulator\x64\Debug'
}

if(!(Test-Path $exe)){ throw "Executable not found: $exe" }

$outLog = Join-Path $logDir ("{0}-{1}.out.log" -f $Mode,$ts)
$errLog = Join-Path $logDir ("{0}-{1}.err.log" -f $Mode,$ts)
$meta   = Join-Path $logDir ("{0}-{1}.meta.log" -f $Mode,$ts)

"START=$(Get-Date -Format o)" | Out-File -FilePath $meta -Encoding utf8
"EXE=$exe" | Out-File -FilePath $meta -Append -Encoding utf8
"WD=$wd" | Out-File -FilePath $meta -Append -Encoding utf8
"CONFIG_PATH=$($resolved.config_path)" | Out-File -FilePath $meta -Append -Encoding utf8
"PROFILE=$($resolved.profile)" | Out-File -FilePath $meta -Append -Encoding utf8
"SHA256=$($resolved.sha256)" | Out-File -FilePath $meta -Append -Encoding utf8

Push-Location $wd
try {
  $p = Start-Process -FilePath $exe -WorkingDirectory $wd -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -WindowStyle Hidden
  "PID=$($p.Id)" | Out-File -FilePath $meta -Append -Encoding utf8
  Wait-Process -Id $p.Id
  $exit = $p.ExitCode
  "END=$(Get-Date -Format o)" | Out-File -FilePath $meta -Append -Encoding utf8
  "EXIT_CODE=$exit" | Out-File -FilePath $meta -Append -Encoding utf8
  Write-Host "DONE mode=$Mode exit=$exit"
  Write-Host "OUT=$outLog"
  Write-Host "ERR=$errLog"
  Write-Host "META=$meta"
  exit $exit
}
finally {
  Pop-Location
}
