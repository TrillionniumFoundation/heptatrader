param(
  [string]$RepoRoot = "D:\quant\HeptaTrader-master"
)

$ErrorActionPreference = 'Stop'
$gitDir = Join-Path $RepoRoot '.git'
$hooksDir = Join-Path $gitDir 'hooks'
$hookPath = Join-Path $hooksDir 'pre-commit'

if(!(Test-Path $gitDir)){
  Write-Host "[WARN] .git not found at $RepoRoot. Initialize git first:" -ForegroundColor Yellow
  Write-Host "       git init"
  exit 1
}

if(!(Test-Path $hooksDir)){ New-Item -ItemType Directory -Path $hooksDir | Out-Null }

$hook = @'
#!/bin/sh
set -eu

# run Hepta secret check before commit
powershell -ExecutionPolicy Bypass -File "./scripts/hepta_secrets_check.ps1" >/dev/null

# block commit if checker fails
if [ $? -ne 0 ]; then
  echo "[pre-commit] Secret check failed. Commit blocked."
  exit 1
fi

echo "[pre-commit] Secret check passed."
'@

Set-Content -LiteralPath $hookPath -Value $hook -Encoding ascii

# best-effort executable bit (on Git Bash environments)
try { & git -C $RepoRoot update-index --chmod=+x .git/hooks/pre-commit 2>$null | Out-Null } catch {}

Write-Host "[OK] Installed pre-commit hook: $hookPath" -ForegroundColor Green
