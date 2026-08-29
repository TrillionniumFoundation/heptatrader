param(
  [string]$ProjectRoot = 'D:\quant\HeptaTrader-master',
  [int]$RecentCommits = 20,
  [switch]$ExportUserEnv
)

$ErrorActionPreference = 'Stop'
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $ProjectRoot ("runtime-logs\governance-$ts")
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$jsonPath = Join-Path $runDir 'governance_summary.json'
$mdPath = Join-Path $runDir 'governance_report.md'

Push-Location $ProjectRoot
try {
  $gitClean = ((git status --porcelain) -join "`n").Trim().Length -eq 0
  $recent = git log --oneline -n $RecentCommits
  $recentCount = @($recent).Count

  $pathHits = @(Get-ChildItem -Path (Join-Path $ProjectRoot 'scripts') -File -Filter *.ps1 -ErrorAction SilentlyContinue |
    Select-String -Pattern 'D:\\quant\\HeptaTrader-master|D:\\VSstudio' -ErrorAction SilentlyContinue)
  $pathHitCount = $pathHits.Count

  $catchHits = @(Get-ChildItem -Path (Join-Path $ProjectRoot 'HeptaTrade') -Recurse -Include *.cpp,*.h -ErrorAction SilentlyContinue |
    Select-String -Pattern 'catch\s*\(\.\.\.\)' -ErrorAction SilentlyContinue)
  $catchCount = $catchHits.Count

  $utf8Pass = $false
  try {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot 'scripts\check_utf8_text.ps1') -ProjectRoot $ProjectRoot | Out-Null
    if($LASTEXITCODE -eq 0){ $utf8Pass = $true }
  } catch { $utf8Pass = $false }

  if($ExportUserEnv){
    try { & powershell -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot 'scripts\export_ib_user_env.ps1') | Out-Null } catch {}
  }

  $envSnapshot = Join-Path $ProjectRoot 'runtime-logs\ib_user_env_snapshot.env'
  $envSnapshotExists = Test-Path $envSnapshot

  $regReports = Get-ChildItem -Path (Join-Path $ProjectRoot 'runtime-logs') -File -Filter 'regression_10m_*.md' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 2
  $reportInfo = @()
  foreach($r in $regReports){
    $overall = ''
    try {
      $line = Select-String -Path $r.FullName -Pattern 'Overall:\s*\*\*([^\*]+)\*\*' | Select-Object -First 1
      if($line){
        $m = [regex]::Match($line.Line, 'Overall:\s*\*\*([^\*]+)\*\*')
        if($m.Success){ $overall = $m.Groups[1].Value }
      }
    } catch {}
    $reportInfo += [ordered]@{ path=$r.FullName; overall=$overall }
  }

  $summary = [ordered]@{
    timestamp = (Get-Date).ToString('o')
    projectRoot = $ProjectRoot
    gitClean = $gitClean
    recentCommitCount = $recentCount
    scriptHardcodedPathHits = $pathHitCount
    catchDotDotDotCount = $catchCount
    utf8CheckPass = $utf8Pass
    envSnapshotPath = $envSnapshot
    envSnapshotExists = $envSnapshotExists
    recent10mReports = $reportInfo
  }

  ($summary | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $jsonPath -Encoding utf8

  $md = New-Object System.Collections.Generic.List[string]
  $md.Add('# Engineering Governance Report')
  $md.Add('')
  $md.Add("- Timestamp: $($summary.timestamp)")
  $md.Add("- ProjectRoot: $ProjectRoot")
  $md.Add("- Git clean: $($summary.gitClean)")
  $md.Add("- Recent commits inspected: $recentCount")
  $md.Add("- Hardcoded path hits in scripts: $pathHitCount")
  $md.Add("- catch(...) count in HeptaTrade: $catchCount")
  $md.Add("- UTF8 check pass: $utf8Pass")
  $md.Add("- Env snapshot exists: $envSnapshotExists")
  $md.Add('')
  $md.Add('## Recent 10m regression reports')
  if($reportInfo.Count -eq 0){ $md.Add('- (none)') }
  else { foreach($ri in $reportInfo){ $md.Add("- $($ri.path) => $($ri.overall)") } }
  $md.Add('')
  $md.Add('## Notes')
  $md.Add('- Governance target: keep git clean, reduce hardcoded paths, minimize catch(...), keep UTF8 checks passing, and archive env snapshots.')
  ($md -join "`r`n") | Set-Content -LiteralPath $mdPath -Encoding utf8

  Write-Host "GOV_RUN_DIR=$runDir"
  Write-Host "GOV_REPORT_MD=$mdPath"
  Write-Host "GOV_SUMMARY_JSON=$jsonPath"
} finally {
  Pop-Location
}
