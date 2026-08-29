param(
  [Parameter(Mandatory=$true)][string]$Template,
  [Parameter(Mandatory=$true)][string]$Output,
  [switch]$AllowExampleOutput
)

$ErrorActionPreference = 'Stop'

if (!(Test-Path -LiteralPath $Template)) { throw "Template not found: $Template" }
if($Output -match '\.example$' -and -not $AllowExampleOutput){
  throw "Refuse to render into .example file: $Output"
}

$required = @(
  'HEPTA_MD_FRONT','HEPTA_TD_FRONT','HEPTA_BROKER_ID','HEPTA_USER_ID',
  'HEPTA_PASSWORD','HEPTA_APP_ID','HEPTA_AUTH_CODE','HEPTA_PRODUCT_INFO'
)

foreach($k in $required){
  $val = [Environment]::GetEnvironmentVariable($k)
  if([string]::IsNullOrWhiteSpace($val)){
    throw "Missing env var: $k"
  }
}

[xml]$xml = Get-Content -LiteralPath $Template -Raw
if($null -eq $xml.Config){ throw 'Invalid template XML: missing <Config> root' }
if($null -eq $xml.Config.User){ throw 'Invalid template XML: missing <User>' }
if($null -eq $xml.Config.User.MarketDataServer){ throw 'Invalid template XML: missing <MarketDataServer>' }
if($null -eq $xml.Config.User.TradeServer){ throw 'Invalid template XML: missing <TradeServer>' }

$xml.Config.User.MarketDataServer.Front = $env:HEPTA_MD_FRONT
$xml.Config.User.MarketDataServer.BrokerID = $env:HEPTA_BROKER_ID
$xml.Config.User.MarketDataServer.UserID = $env:HEPTA_USER_ID
$xml.Config.User.MarketDataServer.PassWord = $env:HEPTA_PASSWORD

$xml.Config.User.TradeServer.Front = $env:HEPTA_TD_FRONT
$xml.Config.User.TradeServer.BrokerID = $env:HEPTA_BROKER_ID
$xml.Config.User.TradeServer.UserID = $env:HEPTA_USER_ID
$xml.Config.User.TradeServer.PassWord = $env:HEPTA_PASSWORD
$xml.Config.User.TradeServer.ProductInfo = $env:HEPTA_PRODUCT_INFO
$xml.Config.User.TradeServer.AppID = $env:HEPTA_APP_ID
$xml.Config.User.TradeServer.AuthCode = $env:HEPTA_AUTH_CODE

$dir = Split-Path -Parent $Output
if(!(Test-Path $dir)){ New-Item -ItemType Directory -Path $dir -Force | Out-Null }

$temp = Join-Path $dir ([IO.Path]::GetRandomFileName() + '.xml')
try {
  $xml.Save($temp)
  Move-Item -LiteralPath $temp -Destination $Output -Force
} finally {
  if(Test-Path -LiteralPath $temp){ Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }
}

Write-Host "Rendered config: $Output"
