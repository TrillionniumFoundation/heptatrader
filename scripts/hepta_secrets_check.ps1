param(
  [string]$Root = "D:\quant\HeptaTrader-master",
  [switch]$StrictEnv
)

$ErrorActionPreference = 'Stop'

Write-Host "[Hepta Secrets Check] Root: $Root"

# 1) Find potential secret leaks in tracked project files
$patterns = @(
  'UserID\s*=\s*"[^"]+"',
  'PassWord\s*=\s*"[^"]+"',
  'Password\s*=\s*"[^"]+"',
  'AuthCode\s*=\s*"[^"]+"',
  'HEPTA_PASSWORD\s*=\s*.+',
  'AKIA[0-9A-Z]{16}',
  'sk-[A-Za-z0-9]+'
)

$exclude = @('*.dll','*.lib','*.so','*.a','*.exe','*.pdb','*.obj','*.png','*.jpg','*.jpeg','*.gif','*.pdf','*.doc','*.docx','*.zip','*.7z')
$files = Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
  $name = $_.Name
  if($name -eq 'HeptaTraderConfig.xml'){ return $false } # local runtime secret config (gitignored)
  -not ($exclude | ForEach-Object { $name -like $_ } | Where-Object { $_ })
}

$hits = @()
foreach($f in $files){
  try {
    $m = Select-String -Path $f.FullName -Pattern $patterns -AllMatches -CaseSensitive:$false -ErrorAction SilentlyContinue
    foreach($x in $m){
      # allow empty template values in .example files
      if($f.Name -like '*.example' -and $x.Line -match 'UserID=""|PassWord=""|AuthCode=""'){ continue }
      $hits += [pscustomobject]@{ File=$f.FullName; Line=$x.LineNumber; Text=$x.Line.Trim() }
    }
  } catch {}
}

if($hits.Count -gt 0){
  Write-Host "[FAIL] Potential secret leaks: $($hits.Count)" -ForegroundColor Red
  $hits | Select-Object -First 100 | Format-Table -Auto
} else {
  Write-Host "[PASS] No obvious secret leaks found" -ForegroundColor Green
}

# 2) Validate template placeholders are not filled
$templatePath = Join-Path $Root 'HeptaTrade\HeptaTraderConfig.xml.example'
$templateIssue = $false
if(Test-Path -LiteralPath $templatePath){
  [xml]$templateXml = Get-Content -LiteralPath $templatePath -Raw
  $sensitiveNodes = @(
    @{ Node=$templateXml.Config.User.MarketDataServer; Keys=@('UserID','PassWord') },
    @{ Node=$templateXml.Config.User.TradeServer; Keys=@('UserID','PassWord','AuthCode') }
  )
  foreach($entry in $sensitiveNodes){
    foreach($k in $entry.Keys){
      $v = "$($entry.Node.$k)"
      if(-not [string]::IsNullOrWhiteSpace($v)){
        Write-Host "[FAIL] Template has non-empty sensitive field: $k" -ForegroundColor Red
        $templateIssue = $true
      }
    }
  }
  if(-not $templateIssue){ Write-Host "[PASS] Template sensitive placeholders are empty" -ForegroundColor Green }
} else {
  Write-Host "[WARN] Template not found: $templatePath" -ForegroundColor Yellow
}

# 3) Check required env vars presence
$required = @(
  'HEPTA_MD_FRONT','HEPTA_TD_FRONT','HEPTA_BROKER_ID','HEPTA_USER_ID',
  'HEPTA_PASSWORD','HEPTA_APP_ID','HEPTA_AUTH_CODE','HEPTA_PRODUCT_INFO'
)

$missing = @()
foreach($k in $required){ if([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($k))){ $missing += $k } }

if($missing.Count -gt 0){
  $level = if($StrictEnv){ 'FAIL' } else { 'WARN' }
  $color = if($StrictEnv){ 'Red' } else { 'Yellow' }
  Write-Host "[$level] Missing environment variables:" -ForegroundColor $color
  $missing | ForEach-Object { Write-Host " - $_" }
} else {
  Write-Host "[PASS] Required environment variables are set" -ForegroundColor Green
}

# 4) Check runtime config existence
$configPath = Join-Path $Root 'HeptaTrade\HeptaTraderConfig.xml'
if(Test-Path -LiteralPath $configPath){
  Write-Host "[OK] Runtime config exists: $configPath"
} else {
  Write-Host "[INFO] Runtime config not found (expected before first render): $configPath"
}

if($hits.Count -gt 0 -or $templateIssue -or ($StrictEnv -and $missing.Count -gt 0)){ exit 2 } else { exit 0 }
