param(
  [string]$Solution = 'D:\quant\HeptaTrader-master\HeptaTrader.sln',
  [string]$VcVars = 'D:\VSstudio\VC\Auxiliary\Build\vcvars64.bat',
  [string]$MSBuild = 'D:\VSstudio\MSBuild\Current\Bin\amd64\MSBuild.exe'
)
$ErrorActionPreference='Stop'

if(!(Test-Path $MSBuild)){ throw "MSBuild not found: $MSBuild" }
if(!(Test-Path $Solution)){ throw "Solution not found: $Solution" }

$repoRoot = Split-Path -Parent $Solution
$ibRoot = $env:IBAPI_ROOT
if([string]::IsNullOrWhiteSpace($ibRoot)){
  $vendored = Join-Path $repoRoot 'Interface\IBApi\source\CppClient'
  if(Test-Path (Join-Path $vendored 'EClient.cpp')){ $ibRoot = $vendored }
}
if([string]::IsNullOrWhiteSpace($ibRoot)){
  $legacy = 'D:\quant\source\CppClient'
  if(Test-Path (Join-Path $legacy 'EClient.cpp')){ $ibRoot = $legacy }
}
if([string]::IsNullOrWhiteSpace($ibRoot)){
  throw 'IBApiRoot unresolved. Set env IBAPI_ROOT or place IB source at Interface\IBApi\source\CppClient.'
}

# Canonical IB build profile entrypoint.
$cmd = "call `"$VcVars`" && `"$MSBuild`" `"$Solution`" /t:Build /m /p:Configuration=Release /p:Platform=x64 /p:HeptaBuildProfile=ib-release /p:IBApiRoot=`"$ibRoot`""
cmd /c $cmd
