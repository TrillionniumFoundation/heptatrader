param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [string]$PythonExe = 'python'
)
$ErrorActionPreference = 'Stop'

$script = Join-Path $ProjectRoot 'scripts\resolve_hepta_config.py'
if(!(Test-Path $script)){ throw "missing: $script" }

$pyCmd = Get-Command $PythonExe -ErrorAction SilentlyContinue
if(-not $pyCmd){
  $pyCmd = Get-Command py -ErrorAction SilentlyContinue
  if(-not $pyCmd){ throw 'Python missing' }
  $PythonExe = 'py'
}

function Run-Resolve([string[]]$ResolveArgsIn){
  $a = @()
  if($PythonExe -eq 'py'){ $a += '-3' }
  $a += @($script) + $ResolveArgsIn

  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = & $PythonExe @a 2>&1
    $code = [int]$LASTEXITCODE
    return [ordered]@{ code = $code; out = ($out -join "`n") }
  } finally {
    $ErrorActionPreference = $oldEap
  }
}

$tmpRoot = Join-Path $env:TEMP ("hepta-resolver-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $tmpRoot 'HeptaTrade') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tmpRoot 'Tools') -Force | Out-Null

$simXml = @'
<Config>
  <Runtime Profile="sim" />
  <IBServer Mode="SIM" Account="" />
</Config>
'@
$paperXml = @'
<Config>
  <Runtime Profile="paper" />
  <IBServer Mode="IB" Account="DU123456" />
</Config>
'@
Set-Content -LiteralPath (Join-Path $tmpRoot 'HeptaTrade\HeptaTraderConfig.xml') -Value $simXml -Encoding UTF8
Set-Content -LiteralPath (Join-Path $tmpRoot 'HeptaTrade\HeptaTraderConfig.xml.example') -Value $paperXml -Encoding UTF8

try {
  $ok = Run-Resolve @('--project-root', $tmpRoot, '--format','json')
  if($ok.code -ne 0){ throw "resolver baseline failed: $($ok.out)" }
  $obj = $ok.out | ConvertFrom-Json
  if([string]::IsNullOrWhiteSpace($obj.config_path) -or [string]::IsNullOrWhiteSpace($obj.profile) -or $obj.sha256.Length -ne 64){
    throw "invalid resolver output: $($ok.out)"
  }
  Write-Host "[PASS] baseline resolve: profile=$($obj.profile) config=$($obj.config_path)"

  $env:HEPTA_CONFIG_PATH = 'C:\tmp\a.xml'
  $env:HEPTA_TRADER_CONFIG_PATH = 'C:\tmp\b.xml'
  $conflict = Run-Resolve @('--project-root', $tmpRoot, '--format','json')
  Remove-Item Env:HEPTA_CONFIG_PATH -ErrorAction SilentlyContinue
  Remove-Item Env:HEPTA_TRADER_CONFIG_PATH -ErrorAction SilentlyContinue
  if($conflict.code -eq 0){ throw 'expected env conflict failure but got success' }
  Write-Host "[PASS] env config conflict fail-fast detected"

  $env:HEPTA_CONFIG_PATH = Join-Path $tmpRoot 'HeptaTrade\HeptaTraderConfig.xml'
  $argConflict = Run-Resolve @('--project-root', $tmpRoot, '--config', (Join-Path $tmpRoot 'Tools\HeptaTraderConfig.xml'), '--format','json')
  Remove-Item Env:HEPTA_CONFIG_PATH -ErrorAction SilentlyContinue
  if($argConflict.code -eq 0){ throw 'expected arg/env config conflict failure but got success' }
  Write-Host "[PASS] arg/env config conflict fail-fast detected"

  Remove-Item -LiteralPath (Join-Path $tmpRoot 'HeptaTrade\HeptaTraderConfig.xml') -Force
  $prodFallback = Run-Resolve @('--project-root', $tmpRoot, '--profile', 'paper', '--format','json')
  if($prodFallback.code -eq 0){ throw 'expected production .example fallback failure but got success' }
  Write-Host "[PASS] production profile blocks .example fallback"

  Write-Host '[PASS] test_config_resolver completed'
}
finally {
  if(Test-Path -LiteralPath $tmpRoot){ Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
