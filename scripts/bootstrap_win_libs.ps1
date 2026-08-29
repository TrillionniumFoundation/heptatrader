[CmdletBinding()]
param(
    [string]$HeptaDllRoot = 'D:\quant\HeptaDLL-main',
    [string]$TraderRoot = 'D:\quant\HeptaTrader-master',
    [string]$Configuration = 'Release',
    [string]$Platform = 'x64',
    [string]$WindowsTargetPlatformVersion = '10.0'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host "[bootstrap-win-libs] $Message"
}

function Resolve-MSBuildPath {
    $cmd = Get-Command msbuild.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) {
        throw "msbuild.exe not found in PATH, and vswhere.exe not found at: $vswhere"
    }

    $msbuild = & $vswhere -latest -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
    if (-not $msbuild) {
        throw 'Unable to locate MSBuild via vswhere.'
    }

    return $msbuild
}

function Invoke-MSBuild {
    param(
        [string]$MSBuildPath,
        [string]$ProjectPath,
        [string[]]$ExtraProperties = @()
    )

    if (-not (Test-Path $ProjectPath)) {
        throw "Project file not found: $ProjectPath"
    }

    $args = @(
        $ProjectPath,
        '/m',
        '/nologo',
        '/verbosity:minimal',
        "/p:Configuration=$Configuration",
        "/p:Platform=$Platform"
    ) + $ExtraProperties

    Write-Step "Building: $ProjectPath"
    & $MSBuildPath @args
    if ($LASTEXITCODE -ne 0) {
        throw "MSBuild failed for $ProjectPath (exit code: $LASTEXITCODE)"
    }
}

function Resolve-FirstExistingPath {
    param([string[]]$Candidates)

    foreach ($p in $Candidates) {
        if (Test-Path $p) { return $p }
    }

    return $null
}

function Copy-LibAndHash {
    param(
        [string]$SourcePath,
        [string]$DestinationDirectory
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Source lib not found: $SourcePath"
    }

    if (-not (Test-Path $DestinationDirectory)) {
        New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
    }

    $destinationPath = Join-Path $DestinationDirectory (Split-Path $SourcePath -Leaf)
    Copy-Item -Path $SourcePath -Destination $destinationPath -Force

    $hash = Get-FileHash -Algorithm SHA256 -Path $destinationPath
    [PSCustomObject]@{
        Name = (Split-Path $destinationPath -Leaf)
        Source = $SourcePath
        Destination = $destinationPath
        SHA256 = $hash.Hash
        SizeBytes = (Get-Item $destinationPath).Length
    }
}

try {
    $msbuildPath = Resolve-MSBuildPath
    Write-Step "Using MSBuild: $msbuildPath"

    $slnPath = Join-Path $HeptaDllRoot 'heptaHeptaDLL.sln'
    $tinyxmlProjPath = Join-Path $HeptaDllRoot 'tinyxml\tinyxml_lib.vcxproj'
    $traderOutDir = Join-Path $TraderRoot 'x64\Release'

    Invoke-MSBuild -MSBuildPath $msbuildPath -ProjectPath $slnPath

    Invoke-MSBuild -MSBuildPath $msbuildPath -ProjectPath $tinyxmlProjPath -ExtraProperties @(
        "/p:WindowsTargetPlatformVersion=$WindowsTargetPlatformVersion"
    )

    $heptaLibPath = Resolve-FirstExistingPath -Candidates @(
        (Join-Path $HeptaDllRoot "x64\$Configuration\heptaHeptaDLL.lib"),
        (Join-Path $HeptaDllRoot "heptaHeptaDLL\x64\$Configuration\heptaHeptaDLL.lib")
    )
    if (-not $heptaLibPath) {
        throw 'Unable to locate heptaHeptaDLL.lib after build.'
    }

    $tinyxmlLibPath = Resolve-FirstExistingPath -Candidates @(
        (Join-Path $HeptaDllRoot "tinyxml\x64\$Configuration\tinyxml.lib"),
        (Join-Path $HeptaDllRoot "x64\$Configuration\tinyxml.lib"),
        (Join-Path $HeptaDllRoot "tinyxml\tinyxml\x64\$Configuration\tinyxml.lib")
    )
    if (-not $tinyxmlLibPath) {
        throw 'Unable to locate tinyxml.lib after build.'
    }

    $results = @(
        (Copy-LibAndHash -SourcePath $heptaLibPath -DestinationDirectory $traderOutDir),
        (Copy-LibAndHash -SourcePath $tinyxmlLibPath -DestinationDirectory $traderOutDir)
    )

    Write-Host ''
    Write-Host '==== Bootstrap Summary ===='
    $results | Format-Table -AutoSize Name, SizeBytes, SHA256
    Write-Host "Output directory: $traderOutDir"
    Write-Host 'STATUS: PASS'
    exit 0
}
catch {
    Write-Error $_
    Write-Host 'STATUS: FAIL'
    exit 1
}
