param(
  [string]$EnvPath = 'D:\quant\HeptaTrader-master\runtime-logs\ib_user_env_snapshot.env'
)
if(!(Test-Path $EnvPath)){ throw "Env file not found: $EnvPath" }
Get-Content -LiteralPath $EnvPath | ForEach-Object {
  $line = $_.Trim()
  if([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')){ return }
  $eq = $line.IndexOf('=')
  if($eq -le 0){ return }
  $k = $line.Substring(0,$eq).Trim()
  $v = $line.Substring($eq+1)
  [Environment]::SetEnvironmentVariable($k,$v,'User')
}
Write-Host "IMPORTED=$EnvPath"
